import asyncio
import os
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile
from dotenv import load_dotenv

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_ENV = SCRIPT_DIR.parent / ".env"


def load_environment() -> None:
    load_dotenv(ROOT_ENV)
    load_dotenv()


def get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name} is not set")
    return value


def normalize_chat_id(raw_value: str) -> int | str:
    value = raw_value.strip()
    if value.startswith("@"):
        return value
    try:
        return int(value)
    except ValueError:
        return value


def build_public_link(chat_username: str | None, message_id: int) -> str | None:
    if not chat_username:
        return None

    normalized = chat_username.strip().lstrip("@")
    if not normalized:
        return None

    return f"https://t.me/{normalized}/{message_id}"


def iter_video_files() -> list[Path]:
    return sorted(
        file_path
        for file_path in SCRIPT_DIR.iterdir()
        if file_path.is_file() and file_path.suffix.lower() in VIDEO_EXTENSIONS
    )


def get_proxy_url() -> str | None:
    for env_name in ("PROXY_URL", "HTTPS_PROXY", "HTTP_PROXY"):
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    return None


async def main() -> None:
    load_environment()

    bot_token = get_required_env("BOT_TOKEN")
    target_chat = normalize_chat_id(get_required_env("VIDEO_TARGET_CHAT"))
    public_chat_username = os.getenv("VIDEO_PUBLIC_CHAT_USERNAME", "").strip() or None
    proxy_url = get_proxy_url()

    video_files = iter_video_files()
    if not video_files:
        raise RuntimeError(f"No video files found in {SCRIPT_DIR}")

    session = AiohttpSession(proxy=proxy_url) if proxy_url else None
    bot = Bot(
        token=bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    try:
        print(f"Found {len(video_files)} video files in {SCRIPT_DIR}")
        print(f"Sending to chat: {target_chat}")
        if proxy_url:
            print("Proxy: enabled")
        else:
            print("Proxy: disabled")
        if public_chat_username:
            print(
                f"Public links will use: https://t.me/{public_chat_username.lstrip('@')}/<message_id>"
            )
        else:
            print(
                "VIDEO_PUBLIC_CHAT_USERNAME is not set. "
                "Only message ids will be printed."
            )

        for video_path in video_files:
            sent_message = await bot.send_video(
                chat_id=target_chat,
                video=FSInputFile(video_path),
                caption=video_path.name,
                supports_streaming=True,
            )

            public_link = build_public_link(
                public_chat_username,
                sent_message.message_id,
            )

            print()
            print(f"File: {video_path.name}")
            print(f"Message ID: {sent_message.message_id}")
            if sent_message.video:
                print(f"File ID: {sent_message.video.file_id}")
            if public_link:
                print(f"Link: {public_link}")
            else:
                print(
                    "Link: unavailable for private chat. "
                    "Set VIDEO_PUBLIC_CHAT_USERNAME for public links."
                )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
