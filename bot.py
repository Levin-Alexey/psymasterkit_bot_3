import asyncio
import os
import json
from datetime import datetime
import asyncpg
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from dotenv import load_dotenv

# Подключаем роутер с тестами
from test_handlers import test_router
from links_handlers import links_router
from take_book_handlers import book_router
from book_followup_handlers import book_followup_router, schedule_book_followup
from diagnostic_handlers import diagnostic_router

load_dotenv()
def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is not set")
    return value

BOT_TOKEN = get_required_env("BOT_TOKEN")
DB_DSN = get_required_env("DB_DSN")
MIN_ACTIVE_MSG_ID = 37

def get_asyncpg_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

dp = Dispatcher()
dp.include_router(test_router)
dp.include_router(links_router)
dp.include_router(book_router)
dp.include_router(book_followup_router)
dp.include_router(diagnostic_router)

# ==========================================
# 1. РАСПИСАНИЕ (Дубль из n8n)
# ==========================================
# Формат: (Год, Месяц, День, Час, Минута): ID_сообщения
SCHEDULE = {
    (2026, 6, 6, 19, 0): 37,
    (2026, 6, 7, 18, 0): 38,
    (2026, 6, 8, 18, 0): 39,
    (2026, 6, 9, 10, 0): 40,
    (2026, 6, 10, 13, 0): 41,
    (2026, 6, 12, 18, 0): 42,
    (2026, 6, 14, 18, 0): 43,
    (2026, 6, 16, 18, 0): 44,
}

def get_current_msg_id() -> int:
    """Определяет, какое сообщение актуально прямо сейчас"""
    current_id = 0
    now = datetime.now() # Берет текущее время сервера
    for (year, month, day, hour, minute), msg_id in SCHEDULE.items():
        dt = datetime(year, month, day, hour, minute)
        if now >= dt:
            current_id = max(current_id, msg_id)
    return current_id

async def add_user_to_db(user_id: int, username: str | None):
    conn = await asyncpg.connect(get_asyncpg_dsn(DB_DSN))
    try:
        await conn.execute("""
            INSERT INTO users_3db (user_id, username)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO NOTHING;
        """, user_id, username or "")

        # Новый пользователь считается уже получившим стартовый экран кампании (msg_id=36).
        await conn.execute("""
            INSERT INTO send_logs_3db (user_id, msg_id)
            VALUES ($1, $2)
            ON CONFLICT (user_id, msg_id) DO NOTHING;
        """, user_id, 36)
        
        # Отсечение 16 мая (msg_id=2 пропускаем — он всегда отправляется новичкам)
        deadline_date = datetime(2026, 5, 16) 
        if datetime.now() >= deadline_date:
            for msg_id in range(1, 18):
                if msg_id == 2:
                    continue
                await conn.execute("""
                    INSERT INTO send_logs_3db (user_id, msg_id)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id, msg_id) DO NOTHING;
                """, user_id, msg_id)
    finally:
        await conn.close()

# ==========================================
# 2. ФУНКЦИЯ ДОГОНКИ НОВИЧКОВ
# ==========================================
async def catch_up_user(user_id: int, bot: Bot):
    # Догонка временно отключена: включим, когда подготовим пул сообщений.
    return

# ==========================================
# 3. ОБРАБОТЧИК /START
# ==========================================
@dp.message(CommandStart())
async def cmd_start(message: Message, bot: Bot): # <-- ВАЖНО: попросили aiogram передать нам bot
    if not message.from_user:
        return

    user_id = message.from_user.id
    username = message.from_user.username
    
    # 1. Сохраняем в базу (и применяем отсечение, если уже 16 мая)
    await add_user_to_db(user_id, username)

    # 2. Показываем первый экран с книгой
    book_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Забрать книгу", callback_data="take_book")]
        ]
    )

    await message.answer(
        "<b>Высылаем вам книгу «Как впустить в свою жизнь большие деньги».</b> Начните с неё спокойно: здесь не будет идеи, что вам нужно ещё сильнее собраться, больше работать или снова искать в себе «что не так».\n\n"
        "Эта книга про другой вопрос: что происходит внутри вас, когда в жизнь начинает приходить больше денег, больше ответственности, больше решений и больше возможностей. Потому что иногда человек головой правда хочет роста, но тело рядом с этим сжимается: появляется тревога, усталость, контроль, откладывание или страх не справиться.\n\n"
        "Чтобы не оставлять это просто теорией, мы предлагаем вам пройти короткий мини-тест. Он займёт меньше минуты и покажет, с чего у вас может начинаться сжатие рядом с деньгами: с тревоги, усталости, откладывания или потери ясности.\n\n"
        "А после теста вы сможете пройти бесплатный разбор «Выдерживает ли твоё тело деньги» с психологом компании и увидеть уже не общий смысл, а вашу личную точку: где именно деньги и рост сейчас ощущаются как нагрузка.",
        reply_markup=book_keyboard,
    )

    try:
        await schedule_book_followup(user_id)
    except Exception as exc:
        print(f"❌ Не удалось запланировать follow-up для user_id={user_id}: {exc}")
    
    # 3. Старое приветственное сообщение отключено для нового события.
    # Оставляем только новый экран с картинкой и кнопкой "Забрать книгу".

    # 4. Досылка временно отключена. Включим позже перед запуском сценария.
    # asyncio.create_task(catch_up_user(user_id, bot))

async def main():
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    print("🤖 Бот запущен (Режим Polling)...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())