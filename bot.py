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

load_dotenv()
def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is not set")
    return value

BOT_TOKEN = get_required_env("BOT_TOKEN")
DB_DSN = get_required_env("DB_DSN")

def get_asyncpg_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

dp = Dispatcher()
dp.include_router(test_router)
dp.include_router(links_router)

# ==========================================
# 1. РАСПИСАНИЕ (Дубль из n8n)
# ==========================================
# Формат: (Год, Месяц, День, Час): ID_сообщения
SCHEDULE = {
    (2026, 5, 5, 10): 1,
    (2026, 5, 6, 10): 2,
    (2026, 5, 7, 10): 3,
    (2026, 5, 7, 11): 4,
    (2026, 5, 8, 10): 5,
    (2026, 5, 8, 11): 6,
    (2026, 5, 8, 12): 7,
    (2026, 5, 9, 10): 8,
    (2026, 5, 9, 11): 9,
    (2026, 5, 10, 10): 10,
    (2026, 5, 10, 11): 11,
    (2026, 5, 11, 10): 12,
    (2026, 5, 12, 10): 13,
    (2026, 5, 13, 10): 14,
    (2026, 5, 14, 10): 15,
    (2026, 5, 15, 10): 16,
    (2026, 5, 16, 10): 17,
    (2026, 5, 16, 11): 18,
    
    # Раскомментируй и добавляй нужные даты!
    # (2026, 5, 6, 13): 5,
    # (2026, 5, 6, 14): 6,
    # (2026, 5, 7, 10): 7,
    # (2026, 5, 16, 10): 15,
}

def get_current_msg_id() -> int:
    """Определяет, какое сообщение актуально прямо сейчас"""
    current_id = 0
    now = datetime.now() # Берет текущее время сервера
    for (year, month, day, hour), msg_id in SCHEDULE.items():
        dt = datetime(year, month, day, hour)
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
        
        # Отсечение 16 мая
        deadline_date = datetime(2026, 5, 16) 
        if datetime.now() >= deadline_date:
            for msg_id in range(1, 11):
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
            AND m.msg_id NOT IN (
                SELECT msg_id FROM send_logs_3db WHERE user_id = $2
            )
            ORDER BY m.msg_id ASC;
        """, current_msg_id, user_id)
        
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
                    await bot.send_photo(chat_id=user_id, photo=msg['image_url'], caption=clean_text, reply_markup=reply_markup)
                else:
                    await bot.send_message(chat_id=user_id, text=clean_text, reply_markup=reply_markup)
                    
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
    
    # 2. Выдаем стандартное приветствие
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

    # 3. ЗАПУСКАЕМ ДОГОНКУ ПАРАЛЛЕЛЬНО!
    # Это не заставит пользователя ждать. Сообщение выше отправится мгновенно, 
    # а скрипт догонки пойдет работать в фоновом режиме.
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