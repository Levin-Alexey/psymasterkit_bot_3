import os
import asyncio
import asyncpg
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from aiogram import Bot
from dotenv import load_dotenv

# Загружаем настройки
load_dotenv()

def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is not set")
    return value

BOT_TOKEN = get_required_env("BOT_TOKEN")
DB_DSN = get_required_env("DB_DSN")
SECRET_N8N_TOKEN = "super_secret_123"  # Замени на свой сложный пароль и укажи его в n8n

def get_asyncpg_dsn(dsn: str) -> str:
    # asyncpg accepts postgresql:// or postgres:// schemes.
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)

app = FastAPI()

# Инициализируем бота ТОЛЬКО для отправки сообщений (без Dispatcher и поллинга)
bot = Bot(token=BOT_TOKEN)

async def run_mailing_in_background(current_msg_id: int):
    """
    Эта функция работает в фоне. Она сама открывает БД, 
    рассылает сообщения с нужными паузами и закрывает БД.
    """
    print(f"🔄 Фоновая рассылка (msg_id={current_msg_id}) запущена...")
    
    # Открываем свое подключение к базе для фоновой задачи
    conn = await asyncpg.connect(get_asyncpg_dsn(DB_DSN))
    try:
        users = await conn.fetch("SELECT user_id FROM users_3db WHERE is_active = true")
        total_sent = 0
        
        for user in users:
            user_id = user['user_id']
            
            # Ищем пропущенные/актуальные сообщения для юзера
            missing_messages = await conn.fetch("""
                SELECT m.msg_id, m.text_content, m.image_url
                FROM messages_3db m
                WHERE m.msg_id <= $1
                AND m.msg_id NOT IN (
                    SELECT msg_id FROM send_logs_3db WHERE user_id = $2
                )
                ORDER BY m.msg_id ASC;
            """, current_msg_id, user_id)
            
            for msg in missing_messages:
                try:
                    # Отправляем сообщение
                    if msg['image_url']:
                        await bot.send_photo(chat_id=user_id, photo=msg['image_url'], caption=msg['text_content'])
                    else:
                        await bot.send_message(chat_id=user_id, text=msg['text_content'])
                    
                    # Фиксируем в логах
                    await conn.execute("""
                        INSERT INTO send_logs_3db (user_id, msg_id)
                        VALUES ($1, $2)
                        ON CONFLICT DO NOTHING;
                    """, user_id, msg['msg_id'])
                    
                    total_sent += 1
                    
                    # 🛑 ЗАЩИТА №1: Пауза между сообщениями ОДНОМУ юзеру
                    # Если человеку летит "догон" из 5 писем, они придут с разницей в 2 секунды.
                    # Это убережет от ошибки Telegram "Too Many Requests: retry after X"
                    await asyncio.sleep(2) 
                    
                except Exception as e:
                    print(f"❌ Ошибка отправки юзеру {user_id}: {e}")
                    # Опционально: если ошибка 'Bot was blocked by the user', 
                    # можно делать UPDATE users_3db SET is_active = false WHERE user_id = $1
                    
            # 🛑 ЗАЩИТА №2: Пауза между РАЗНЫМИ юзерами
            # Лимит Telegram - 30 сообщений в секунду всем пользователям.
            # 0.05 секунды = максимум 20 пользователей в секунду. Мы в полной безопасности.
            await asyncio.sleep(0.05)
            
        print(f"✅ Фоновая рассылка завершена! Отправлено сообщений: {total_sent}")
    
    finally:
        # Обязательно закрываем соединение после конца рассылки
        await conn.close()

@app.post("/webhook/trigger-mailing")
async def trigger_mailing(
    background_tasks: BackgroundTasks,
    token: str = Query(...),
    current_msg_id: int = Query(...)
):
    """
    Этот эндпоинт дергает n8n.
    """
    if token != SECRET_N8N_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")

    # Передаем рассылку в фоновую задачу и сразу отвечаем n8n.
    background_tasks.add_task(run_mailing_in_background, current_msg_id)

    return {
        "status": "ok",
        "message": f"Рассылка до msg_id={current_msg_id} запущена в фоне!"
    }