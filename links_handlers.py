from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

links_router = Router()


def build_link_keyboard(button_text: str, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=button_text, url=url)]]
    )


@links_router.message(Command("ticket"))
async def cmd_ticket(message: Message):
    await message.answer(
        "Открыть страницу покупки билета на Open Day:",
        reply_markup=build_link_keyboard(
            button_text="Открыть билет",
            url="https://super-ego.info/openday2026/?src=vt",
        ),
    )


@links_router.message(Command("channel"))
async def cmd_channel(message: Message):
    await message.answer(
        "Открыть канал Open Day:",
        reply_markup=build_link_keyboard(
            button_text="Открыть канал", url="https://t.me/freemoneymasterkit/7169"
        ),
    )


@links_router.message(Command("question"))
async def cmd_question(message: Message):
    await message.answer(
        "Открыть чат поддержки:",
        reply_markup=build_link_keyboard(
            button_text="Задать вопрос", url="https://t.me/mkhelper"
        ),
    )


@links_router.message(Command("darlanding"))
async def cmd_dar_landing(message: Message):
    await message.answer(
        "Узнайте подробнее о сообществе DAR:",
        reply_markup=build_link_keyboard(
            button_text="Узнать подробнее",
            url="https://super-ego.info/dar/",
        ),
    )
