import asyncio
import os
from datetime import datetime
import asyncpg
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message
from aiogram.filters import CommandStart
from dotenv import load_dotenv

# Загружаем настройки из .env
load_dotenv()
def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is not set")
    return value

BOT_TOKEN = get_required_env("BOT_TOKEN")
DB_DSN = get_required_env("DB_DSN")

def get_asyncpg_dsn(dsn: str) -> str:
    # asyncpg accepts postgresql:// or postgres:// schemes.
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

dp = Dispatcher()

async def add_user_to_db(user_id: int, username: str | None):
    """
    Добавляет пользователя в БД. 
    Если он пришел 16 мая или позже - заполняет историю старыми сообщениями (отсечение).
    """
    conn = await asyncpg.connect(get_asyncpg_dsn(DB_DSN))
    try:
        # 1. Записываем пользователя (или игнорируем, если он уже есть)
        await conn.execute("""
            INSERT INTO users_3db (user_id, username)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO NOTHING;
        """, user_id, username or "")
        
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
    if not message.from_user:
        return

    user_id = message.from_user.id
    username = message.from_user.username
    
    # Сохраняем в БД с учетом проверки на 16 мая
    await add_user_to_db(user_id, username)
    
    # Приветственное сообщение
    await message.answer_photo(
        photo="https://www.image2url.com/r2/default/images/1778044299365-490eb501-c7dc-4743-b03e-3b0f1cc03bc7.png",
    )

    await message.answer(
        "<b>Вы когда-нибудь задумывались, почему одни и те же ситуации повторяются, даже когда вы стараетесь действовать иначе?</b>\n\n"
        "Вроде бы уже все по-другому делаешь.\n\n"
        "Начали больше зарабатывать, а с деньгами все равно напряжение: то не хватает, то со счета на карту перебрасываешь, то кредиткой закрываешь одно, думая «сейчас разгребу, это просто месяц такой».\n\n"
        "В отношениях тоже: обещаешь себе, что в этот раз все будет иначе, внимательнее смотришь на человека, но в итоге все равно приходишь примерно в ту же точку.\n\n"
        "И с работой похожая история. Находишь что-то новое, сначала загораешься, а потом проходит время и снова ощущение, что не то, не твое.\n\n"
        "И в какой-то момент начинаешь думать:\n"
        "<b>почему так происходит, если я правда стараюсь жить по-другому?</b>\n\n"
        "Дело в том, что чаще всего мы меняем действия, но остаемся в той же внутренней роли.\n\n"
        "В психологии Master Kit это называют <b>Персоной</b>.\n\n"
        "Это не характер, а привычная модель поведения, через которую вы живете. Когда-то она вам очень помогла быть сильным, справляться, держать все под контролем и не сдаваться.\n\n"
        "Но со временем она начинает работать автоматически. И даже когда вы хотите по-другому - автоматически включается тот же сценарий.\n\n"
        "Поэтому внешне многое меняется, а результаты в итоге остаются примерно таким же.\n\n"
        "Но хорошая новость в том, что это можно увидеть.\n\n"
        "А когда вы это видите - появляется возможность выйти из этого круга.\n\n"
        "Как раз про это мы будем говорить дальше👇🏼",
    )

async def main():
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    print("🤖 Бот запущен (Режим Polling)...")
    try:
        # Запускаем прослушку Telegram
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())