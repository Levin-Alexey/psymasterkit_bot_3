import os
import asyncio
import json
from pathlib import Path
import asyncpg
import traceback  # <--- ВАЖНО: для отлова скрытых ошибок
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    InputMediaVideo,
)
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
START_MSG_ID = 1
MIN_ACTIVE_MSG_ID = 1
SPECIAL_MEDIA_MESSAGE_ID = int(os.getenv("SPECIAL_MEDIA_MESSAGE_ID", "1"))
SPECIAL_MEDIA_FILE_PATH = os.getenv("SPECIAL_MEDIA_FILE_PATH", "media_file_ids.json")


def get_asyncpg_dsn(dsn: str) -> str:
    # asyncpg accepts postgresql:// or postgres:// schemes.
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


app = FastAPI()

# Инициализируем бота ТОЛЬКО для отправки сообщений (без Dispatcher и поллинга)
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)


def load_special_media_items() -> list[dict]:
    file_path = Path(SPECIAL_MEDIA_FILE_PATH)
    if not file_path.exists():
        return []

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    items = []
    for item in data.get("photos", []):
        if item.get("file_id"):
            items.append({"type": "photo", "file_id": item["file_id"]})
    for item in data.get("videos", []):
        if item.get("file_id"):
            items.append({"type": "video", "file_id": item["file_id"]})
    return items


def build_special_media_group(items: list[dict]):
    media = []
    for item in items:
        if item["type"] == "photo":
            media.append(InputMediaPhoto(media=item["file_id"]))
        elif item["type"] == "video":
            media.append(
                InputMediaVideo(
                    media=item["file_id"],
                    supports_streaming=True,
                )
            )
    return media


SPECIAL_MEDIA_ITEMS = load_special_media_items()


async def run_mailing_in_background(
    current_msg_id: int, target_user_id: int | None = None
):
    """
    Эта функция работает в фоне. Она сама открывает БД,
    рассылает сообщения с нужными паузами и закрывает БД.
    """
    if current_msg_id < START_MSG_ID:
        print(
            f"⛔ Пропуск рассылки: current_msg_id={current_msg_id} < "
            f"START_MSG_ID={START_MSG_ID}",
            flush=True,
        )
        return

    effective_min_msg_id = MIN_ACTIVE_MSG_ID

    # flush=True заставляет логи появляться мгновенно
    print(f"🔄 Фоновая рассылка (msg_id={current_msg_id}) запущена...", flush=True)

    try:
        # Открываем свое подключение к базе для фоновой задачи
        conn = await asyncpg.connect(get_asyncpg_dsn(DB_DSN))
        try:
            if target_user_id is not None:
                users = await conn.fetch(
                    "SELECT user_id FROM users_3db WHERE is_active = true AND user_id = $1",
                    target_user_id,
                )
            else:
                users = await conn.fetch(
                    "SELECT user_id FROM users_3db WHERE is_active = true"
                )
            total_sent = 0

            for user in users:
                user_id = user["user_id"]

                # Ищем пропущенные/актуальные сообщения для юзера
                missing_messages = await conn.fetch(
                    """
                    SELECT m.msg_id, m.text_content, m.image_url, m.inline_buttons
                    FROM messages_3db m
                    WHERE m.msg_id <= $1
                    AND m.msg_id >= $3
                    AND m.msg_id NOT IN (
                        SELECT msg_id FROM send_logs_3db WHERE user_id = $2
                    )
                    ORDER BY m.msg_id ASC;
                """,
                    current_msg_id,
                    user_id,
                    effective_min_msg_id,
                )

                for msg in missing_messages:
                    try:
                        # 1. СОБИРАЕМ КЛАВИАТУРУ
                        reply_markup = None
                        if msg["inline_buttons"]:
                            buttons_data = (
                                json.loads(msg["inline_buttons"])
                                if isinstance(msg["inline_buttons"], str)
                                else msg["inline_buttons"]
                            )

                            keyboard = []
                            for row in buttons_data:
                                kb_row = []
                                for btn in row:
                                    kb_row.append(
                                        InlineKeyboardButton(
                                            text=btn["text"],
                                            callback_data=btn.get("callback_data"),
                                            url=btn.get("url"),
                                        )
                                    )
                                keyboard.append(kb_row)
                            reply_markup = InlineKeyboardMarkup(
                                inline_keyboard=keyboard
                            )

                        # БРОНЯ: чистим текстовые слеши \n в реальные абзацы
                        clean_text = msg["text_content"].replace("\\n", "\n")

                        if msg["msg_id"] == SPECIAL_MEDIA_MESSAGE_ID:
                            photo_items = [
                                item
                                for item in SPECIAL_MEDIA_ITEMS
                                if item["type"] == "photo"
                            ]
                            media_group = build_special_media_group(photo_items)
                            if media_group:
                                await bot.send_media_group(
                                    chat_id=user_id,
                                    media=media_group,
                                )
                                await asyncio.sleep(0.6)
                                await bot.send_message(
                                    chat_id=user_id,
                                    text=clean_text,
                                    reply_markup=reply_markup,
                                )
                                await conn.execute(
                                    """
                                    INSERT INTO send_logs_3db (user_id, msg_id)
                                    VALUES ($1, $2)
                                    ON CONFLICT DO NOTHING;
                                    """,
                                    user_id,
                                    msg["msg_id"],
                                )
                                total_sent += 1
                                await asyncio.sleep(2)
                                continue

                        # 2. ОТПРАВЛЯЕМ СООБЩЕНИЕ
                        if msg["image_url"]:
                            # Проверяем длину текста для картинки
                            if len(clean_text) <= 1024:
                                # Если текст влезает, отправляем одним куском (картинка + текст + кнопки)
                                await bot.send_photo(
                                    chat_id=user_id,
                                    photo=msg["image_url"],
                                    caption=clean_text,
                                    reply_markup=reply_markup,
                                )
                            else:
                                # ТЕКСТ СЛИШКОМ ДЛИННЫЙ!
                                # Отправляем сначала голую картинку...
                                await bot.send_photo(
                                    chat_id=user_id, photo=msg["image_url"]
                                )
                                # ...а затем сразу обычное текстовое сообщение (с кнопками)
                                await bot.send_message(
                                    chat_id=user_id,
                                    text=clean_text,
                                    reply_markup=reply_markup,
                                )
                        else:
                            # Обычный текст без картинки
                            await bot.send_message(
                                chat_id=user_id,
                                text=clean_text,
                                reply_markup=reply_markup,
                            )

                        # Фиксируем в логах
                        await conn.execute(
                            """
                            INSERT INTO send_logs_3db (user_id, msg_id)
                            VALUES ($1, $2)
                            ON CONFLICT DO NOTHING;
                        """,
                            user_id,
                            msg["msg_id"],
                        )

                        total_sent += 1
                        print(
                            f"✉️ Успешно отправлено msg_{msg['msg_id']} юзеру {user_id}",
                            flush=True,
                        )

                        # Пауза между сообщениями ОДНОМУ юзеру
                        await asyncio.sleep(2)

                    except Exception as e:
                        print(f"❌ Ошибка отправки юзеру {user_id}: {e}", flush=True)

                # Пауза между РАЗНЫМИ юзерами
                await asyncio.sleep(0.05)

            print(
                f"✅ Фоновая рассылка завершена! Отправлено сообщений: {total_sent}",
                flush=True,
            )

        finally:
            await conn.close()

    except Exception as e:
        # ЕСЛИ ЧТО-ТО УПАДЕТ ГЛОБАЛЬНО - МЫ ЭТО УВИДИМ
        print(f"💥 КРИТИЧЕСКАЯ ОШИБКА ФОНОВОЙ ЗАДАЧИ: {e}", flush=True)
        traceback.print_exc()


@app.post("/webhook/trigger-mailing")
async def trigger_mailing(
    background_tasks: BackgroundTasks,
    token: str = Query(...),
    current_msg_id: int = Query(...),
):
    """
    Этот эндпоинт дергает n8n.
    """
    if token != SECRET_N8N_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")

    if current_msg_id < START_MSG_ID:
        raise HTTPException(
            status_code=400,
            detail=(
                f"current_msg_id must be >= {START_MSG_ID} " "for the current campaign"
            ),
        )

    # Передаем рассылку в фоновую задачу
    background_tasks.add_task(run_mailing_in_background, current_msg_id, None)

    return {
        "status": "ok",
        "message": f"Рассылка до msg_id={current_msg_id} запущена в фоне!",
    }


@app.get("/mailing")
async def mailing(
    background_tasks: BackgroundTasks,
    current_msg_id: int = Query(...),
    user_id: int | None = Query(default=None),
):
    """Запуск досылки: либо всем, либо конкретному пользователю."""
    if current_msg_id < START_MSG_ID:
        raise HTTPException(
            status_code=400,
            detail=(
                f"current_msg_id must be >= {START_MSG_ID} " "for the current campaign"
            ),
        )

    background_tasks.add_task(run_mailing_in_background, current_msg_id, user_id)

    return {
        "status": "ok",
        "message": (
            f"Рассылка до msg_id={current_msg_id} запущена в фоне "
            f"для user_id={user_id}."
            if user_id is not None
            else f"Рассылка до msg_id={current_msg_id} запущена в фоне для всех пользователей."
        ),
    }
