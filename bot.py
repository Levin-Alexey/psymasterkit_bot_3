import asyncio
import os
from datetime import datetime
import asyncpg
import aiohttp
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
MAILING_API_BASE_URL = os.getenv("MAILING_API_BASE_URL", "http://127.0.0.1:8000")


def get_asyncpg_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


dp = Dispatcher()
dp.include_router(test_router)
dp.include_router(links_router)
dp.include_router(book_router)
dp.include_router(book_followup_router)
dp.include_router(diagnostic_router)


# Формат: (Год, Месяц, День, Час, Минута): ID_сообщения
# Первая рассылка кампании: 14.08.2026 12:00 -> msg_id=1.
SCHEDULE = {
    (2026, 8, 14, 12, 0): 1,
}


def get_current_msg_id() -> int:
    """Определяет текущий msg_id на основе расписания кампании."""
    now = datetime.now()

    # Ищем последнюю наступившую точку расписания.
    last_dt = None
    last_msg_id = 0
    for (year, month, day, hour, minute), msg_id in SCHEDULE.items():
        dt = datetime(year, month, day, hour, minute)
        if now >= dt and (last_dt is None or dt > last_dt):
            last_dt = dt
            last_msg_id = msg_id

    # До старта кампании не отправляем сообщения из базы.
    if last_dt is None:
        return 0

    # После первой точки: строго +1 msg_id в сутки.
    return last_msg_id + (now - last_dt).days


async def add_user_to_db(user_id: int, username: str | None):
    conn = await asyncpg.connect(get_asyncpg_dsn(DB_DSN))
    try:
        await conn.execute(
            """
            INSERT INTO users_3db (user_id, username)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO NOTHING;
        """,
            user_id,
            username or "",
        )
    finally:
        await conn.close()


# ==========================================
# 2. ФУНКЦИЯ ДОСЫЛКИ
# ==========================================
async def catch_up_user(user_id: int):
    """Отправляет пропущенные сообщения через API /mailing"""
    try:
        current_msg_id = get_current_msg_id()
        if current_msg_id <= 0:
            return

        base_url = MAILING_API_BASE_URL.rstrip("/")
        url = f"{base_url}/mailing?current_msg_id={current_msg_id}&user_id={user_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    print(f"✅ Досылка для user_id={user_id}: msg_id={current_msg_id}")
    except Exception as exc:
        print(f"⚠️  Досылка ошибка: {exc}")


# ==========================================
# 3. ОБРАБОТЧИК /START
# ==========================================
@dp.message(CommandStart())
async def cmd_start(
    message: Message, bot: Bot
):  # <-- ВАЖНО: попросили aiogram передать нам bot
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

    # Досылка пропущенных сообщений
    _catch_up_task = asyncio.create_task(catch_up_user(user_id))


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
