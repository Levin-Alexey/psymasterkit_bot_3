import asyncio
import json
import os
from pathlib import Path
from datetime import datetime
import asyncpg
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
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
MEDIA_FILE_IDS_PATH = os.getenv("SPECIAL_MEDIA_FILE_PATH", "media_file_ids.json")


def get_asyncpg_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1)


dp = Dispatcher()
dp.include_router(test_router)
dp.include_router(links_router)
dp.include_router(book_router)
dp.include_router(book_followup_router)
dp.include_router(diagnostic_router)


def load_media_group() -> list[InputMediaPhoto | InputMediaVideo]:
    file_path = Path(MEDIA_FILE_IDS_PATH)
    if not file_path.exists():
        return []

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    media_group: list[InputMediaPhoto | InputMediaVideo] = []
    for item in data.get("photos", []):
        file_id = item.get("file_id")
        if file_id:
            media_group.append(InputMediaPhoto(media=file_id))

    for item in data.get("videos", []):
        file_id = item.get("file_id")
        if file_id:
            media_group.append(InputMediaVideo(media=file_id, supports_streaming=True))

    return media_group


async def send_motivation_media(bot: Bot, chat_id: int) -> None:
    media_group = load_media_group()
    if media_group:
        await bot.send_media_group(chat_id=chat_id, media=media_group)


# Формат: (Год, Месяц, День, Час, Минута): ID_сообщения
# Первая рассылка кампании: 14.08.2026 12:00 -> msg_id=1.
SCHEDULE = {
    (2026, 8, 14, 12, 0): 1,
    (2026, 8, 15, 12, 0): 2,
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

    # 2. Показываем первое стартовое сообщение
    start_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Вроде знаю, что делать, но жизнь не меняется",
                    callback_data="motivation_stagnation",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Опять скатилась в рутину после ярких событий",
                    callback_data="motivation_routine",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Хочу двигаться к целям легко, без надрыва",
                    callback_data="motivation_easy_goals",
                )
            ],
        ]
    )

    await message.answer(
        "<b>Помните это яркое ощущение, когда мы чем-то невероятно загораемся?</b>\n\n"
        "Чувствуем мощный приток энергии и искренне верим: «Всё, теперь-то жизнь точно изменится!».\n\n"
        "🪫<b>Но проходит несколько месяцев… Яркие эмоции утихают, а на смену им приходит привычная рутина.</b>\n\n"
        "Это знакомый каждому сценарий: нас легко может зарядить какое-то новое обучение, вдохновляющая встреча или интересная идея. В моменты такого подъема кажется, что горы можно сдвинуть. Но стоит вернуться в привычный водоворот забот, где дом, работа и ежедневные дела затягивают с головой, как это пламя начинает тихонечко гаснуть.\n\n"
        "Вдохновение растворяется в быте, батарейка садится, и от грандиозных планов остается лишь легкая грусть. И ты снова обнаруживаешь себя в той же точке, где всё началось - <b>без реальных изменений в жизни, о которых так мечтала</b>.\n\n"
        "📍Почему так происходит, как перестать сливать мотивацию в пустоту и сделать так, чтобы энергия наконец-то приводила к осязаемым результатам в жизни? — Давайте разбираться ⤵️\n\n"
        "<b>Какая фраза точнее всего описывает вашу точку «сейчас»?</b>",
        reply_markup=start_keyboard,
    )

    try:
        await schedule_book_followup(user_id)
    except Exception as exc:
        print(f"❌ Не удалось запланировать follow-up для user_id={user_id}: {exc}")

    # Досылка пропущенных сообщений
    _catch_up_task = asyncio.create_task(catch_up_user(user_id))


@dp.callback_query(F.data == "motivation_stagnation")
async def handle_motivation_stagnation(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    if not callback.message:
        return

    await send_motivation_media(bot, callback.from_user.id)
    await callback.message.answer(
        "<b>Мы привыкли думать, что дело в нас самих:</b> мол, недостаточно "
        "силы воли, плохо старались, поленились.\n\n"
        "Но правда в том, что даже самый мощный заряд энергии неизбежно "
        "угаснет, если вариться в нем в полном одиночестве. Вспомните: "
        "когда вы возвращаетесь в свой обычный круг общения, к своим "
        "привычным заботам, где никто не разделяет ваши интересы и мысли — "
        "этот внутренний огонь просто задыхается без поддержки.\n\n"
        "<b>Одному расти невероятно тяжело</b>. Когда нет поля "
        "единомышленников, нет живого зеркала и тех, кто идет рядом, "
        "любая искра гаснет под грузом быта 💔\n\n"
        "Именно поэтому <b>мы создали пространство, где этот закон больше "
        "не работает.</b> Это не просто чат и не курс. Это живое поле, "
        "устроенное совсем по-другому — так, чтобы в нем не нужно было "
        "пахать, доказывать что-то или тащить всё на себе.\n\n"
        "Там работает совершенно другая механика, которая бережно держит "
        "вас в ресурсе, и <b>помогает шаг за шагом менять жизнь</b> даже "
        "тогда, когда вокруг штормит ⤵️\n\n"
        "<b>Хотите узнать, что это за место и как оно работает?</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Да, покажите подробности",
                        url="https://super-ego.info/dar/",
                    )
                ]
            ]
        ),
    )


@dp.callback_query(F.data == "motivation_routine")
async def handle_motivation_routine(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    if callback.message:
        await send_motivation_media(bot, callback.from_user.id)


@dp.callback_query(F.data == "motivation_easy_goals")
async def handle_motivation_easy_goals(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    if callback.message:
        await send_motivation_media(bot, callback.from_user.id)


@dp.callback_query(F.data == "watch_meeting_recording")
async def show_meeting_recording_options(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "Выберите, где посмотреть запись встречи:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Смотреть на YouTube",
                        url="https://youtu.be/Jvkl7wwa0Z0",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Смотреть в VK",
                        url="https://vkvideo.ru/video-90499927_456240086",
                    )
                ],
            ]
        ),
    )


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
