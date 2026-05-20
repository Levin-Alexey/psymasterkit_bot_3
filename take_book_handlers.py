import os
import asyncpg
import aiohttp
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv

load_dotenv()
DB_DSN = os.getenv("DB_DSN")


def get_asyncpg_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1) if dsn else ""


book_router = Router()


class BookPhoneState(StatesGroup):
    waiting_for_phone = State()
    waiting_for_confirmation = State()


@book_router.callback_query(F.data == "take_book")
async def ask_for_book_phone(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer(
        "Оставьте свой номер телефона, и мы вышлем книгу «Лабиринт, у которого нет стен»"
    )
    await state.set_state(BookPhoneState.waiting_for_phone)


@book_router.message(Command("take_book"))
async def cmd_take_book(message: Message, state: FSMContext):
    await message.answer(
        "Оставьте свой номер телефона, и мы вышлем книгу «Лабиринт, у которого нет стен»"
    )
    await state.set_state(BookPhoneState.waiting_for_phone)


@book_router.message(BookPhoneState.waiting_for_phone)
async def process_book_phone_input(message: Message, state: FSMContext):
    phone = (message.text or "").strip()
    if not phone:
        await message.answer("Пожалуйста, введите номер телефона текстом.")
        return

    await state.update_data(phone=phone)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Номер введен правильно", callback_data="phone_correct_book")],
            [InlineKeyboardButton(text="✏️ Исправить номер", callback_data="phone_edit_book")],
        ]
    )

    await message.answer(f"Вы ввели номер: <b>{phone}</b>\n\nВсё верно?", reply_markup=kb)
    await state.set_state(BookPhoneState.waiting_for_confirmation)


@book_router.callback_query(F.data == "phone_edit_book", BookPhoneState.waiting_for_confirmation)
async def edit_book_phone(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("Пожалуйста, введите ваш номер телефона еще раз:")
    await state.set_state(BookPhoneState.waiting_for_phone)


@book_router.callback_query(F.data == "phone_correct_book", BookPhoneState.waiting_for_confirmation)
async def confirm_book_phone(callback: CallbackQuery, state: FSMContext):
    await callback.answer()

    user_data = await state.get_data()
    phone = user_data.get("phone")
    user_id = callback.from_user.id
    username = callback.from_user.username

    conn = await asyncpg.connect(get_asyncpg_dsn(DB_DSN))
    try:
        await conn.execute(
            """
            UPDATE users_3db
            SET phone = $1
            WHERE user_id = $2;
        """,
            phone,
            user_id,
        )

        scores_row = await conn.fetchrow(
            """
            SELECT finance_score, purpose_score, relations_score, life_score
            FROM test_results_3db
            WHERE user_id = $1;
        """,
            user_id,
        )
    finally:
        await conn.close()

    scores = {
        "finance_score": 0,
        "purpose_score": 0,
        "relations_score": 0,
        "life_score": 0,
    }
    if scores_row:
        scores = {
            "finance_score": scores_row["finance_score"],
            "purpose_score": scores_row["purpose_score"],
            "relations_score": scores_row["relations_score"],
            "life_score": scores_row["life_score"],
        }

    webhook_url = "https://superegocomp.app.n8n.cloud/webhook/bot3"
    payload = {
        "user_id": user_id,
        "username": username,
        "phone": phone,
        "finance_score": scores["finance_score"],
        "purpose_score": scores["purpose_score"],
        "relations_score": scores["relations_score"],
        "life_score": scores["life_score"],
        "action": "take_book",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as response:
                print(f"n8n webhook status: {response.status}")
    except Exception as e:
        print(f"❌ Ошибка отправки вебхука в n8n: {e}")

    kb_book = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Забрать книгу",
                    url="https://t.me/Masterkit7_bot?start=c1779023999229-ds",
                )
            ]
        ]
    )

    await callback.message.answer("Спасибо, желаем приятного чтения и быстрых трансформаций ❤️", reply_markup=kb_book)
    await state.clear()
