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
    current_msg_id = get_current_msg_id()
    if current_msg_id == 0:
        return # Еще не было ни одной рассылки, догонять нечем
        
    # Небольшая пауза перед тем, как закидывать письмами (чтобы юзер успел прочитать welcome-сообщение)
    await asyncio.sleep(3)
    
    conn = await asyncpg.connect(get_asyncpg_dsn(DB_DSN))
    try:
        missing_messages = await conn.fetch("""
            SELECT m.msg_id, m.text_content, m.image_url, m.inline_buttons
            FROM messages_3db m
            WHERE m.msg_id <= $1
            AND m.msg_id >= $3
            AND m.msg_id NOT IN (
                SELECT msg_id FROM send_logs_3db WHERE user_id = $2
            )
            ORDER BY m.msg_id ASC;
        """, current_msg_id, user_id, MIN_ACTIVE_MSG_ID)
        
        for msg in missing_messages:
            try:
                reply_markup = None
                if msg['inline_buttons']:
                    buttons_data = json.loads(msg['inline_buttons']) if isinstance(msg['inline_buttons'], str) else msg['inline_buttons']
                    keyboard = []
                    for row in buttons_data:
                        kb_row = []
                        for btn in row:
                            kb_row.append(InlineKeyboardButton(text=btn['text'], callback_data=btn.get('callback_data'), url=btn.get('url')))
                        keyboard.append(kb_row)
                    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

                # Чистим \n
                clean_text = msg['text_content'].replace('\\n', '\n')
                
                # Отправляем
                if msg['image_url']:
                    # Проверяем лимит Телеграма (1024 символа для картинки)
                    if len(clean_text) <= 1024:
                        await bot.send_photo(
                            chat_id=user_id, 
                            photo=msg['image_url'], 
                            caption=clean_text, 
                            reply_markup=reply_markup
                        )
                    else:
                        # Текст слишком длинный: шлем картинку, потом текст с кнопками
                        await bot.send_photo(
                            chat_id=user_id, 
                            photo=msg['image_url']
                        )
                        await bot.send_message(
                            chat_id=user_id, 
                            text=clean_text, 
                            reply_markup=reply_markup
                        )
                else:
                    await bot.send_message(
                        chat_id=user_id, 
                        text=clean_text, 
                        reply_markup=reply_markup
                    )
                    
                # Пишем в лог
                await conn.execute("""
                    INSERT INTO send_logs_3db (user_id, msg_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING;
                """, user_id, msg['msg_id'])
                
                await asyncio.sleep(2) # Защита от спам-блока Telegram
            except Exception as e:
                print(f"❌ Ошибка догонки юзеру {user_id}: {e}")
    finally:
        await conn.close()

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

    await message.answer_photo(
        photo="https://www.image2url.com/r2/default/images/1780728336729-933507e2-3a7b-4365-8f40-07e0ec75f537.png",
        caption=(
            "<b>💰 Этот финансовый инструмент должен быть у каждого, мы дарим его вам!</b>\n\n"
            "Большинство людей ищут способы заработать больше: осваивают новые профессии, ищут дополнительные источники доход.\n\n"
            "Но редко задаются вопросом: Почему при одинаковых возможностях одни растут, а другие годами остаются на месте?\n\n"
            "Дело не в знаниях и не в количестве усилий. А во внутренних программах и автоматизмах, которые управляют нашими решениями каждый день.\n\n"
            "🎁 Книга Дарьи Трутневой «Как впустить большие деньги в свою жизнь» помогает увидеть эти сценарии, найти свои финансовые ограничения и по-новому посмотреть на отношения с деньгами.\n\n"
            "И прямо сейчас вы можете получить её бесплатно в аудиоформате."
        ),
        reply_markup=book_keyboard,
    )
    
    # 3. Старое приветственное сообщение отключено для нового события.
    # Оставляем только новый экран с картинкой и кнопкой "Забрать книгу".

    # 4. Запускаем догонку новых подписчиков по новому потоку (msg_id >= 37).
    asyncio.create_task(catch_up_user(user_id, bot))

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