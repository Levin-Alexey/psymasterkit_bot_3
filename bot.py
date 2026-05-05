import asyncio
import os
from datetime import datetime
import asyncpg
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart
from dotenv import load_dotenv

# Загружаем настройки из .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_DSN = os.getenv("DB_DSN")

dp = Dispatcher()

async def add_user_to_db(user_id: int, username: str):
    """
    Добавляет пользователя в БД. 
    Если он пришел 16 мая или позже - заполняет историю старыми сообщениями (отсечение).
    """
    conn = await asyncpg.connect(DB_DSN)
    try:
        # 1. Записываем пользователя (или игнорируем, если он уже есть)
        await conn.execute("""
            INSERT INTO users_3db (user_id, username)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO NOTHING;
        """, user_id, username)
        
        # 2. ЛОГИКА ОТСЕЧЕНИЯ СТАРЫХ ПИСЕМ (16 мая)
        # Внимание: проверь год (2026 или 2024), чтобы он совпадал с датами твоего запуска!
        deadline_date = datetime(2026, 5, 16) 
        
        if datetime.now() >= deadline_date:
            # Юзер пришел поздно. Имитируем, что он уже получил прогрев (письма 1-10)
            for msg_id in range(1, 11):
                await conn.execute("""
                    INSERT INTO send_logs_3db (user_id, msg_id)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id, msg_id) DO NOTHING;
                """, user_id, msg_id)
    finally:
        await conn.close()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username
    
    # Сохраняем в БД с учетом проверки на 16 мая
    await add_user_to_db(user_id, username)
    
    # Приветственное сообщение
    await message.answer(
        "Привет! Ты успешно зарегистрирован на тренинг Дарьи Трутневой.\n\n"
        "Скоро мы начнем погружение в лабиринт!"
    )

async def main():
    bot = Bot(token=BOT_TOKEN)
    print("🤖 Бот запущен (Режим Polling)...")
    try:
        # Запускаем прослушку Telegram
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())