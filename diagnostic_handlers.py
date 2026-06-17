import json
import os

import aiohttp
import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

load_dotenv()
DB_DSN = os.getenv("DB_DSN")

diagnostic_router = Router()

DIAGNOSTIC_REMINDER_20M_SCENARIO = "diagnostic_reminder_20m"
DIAGNOSTIC_REMINDER_2H_SCENARIO = "diagnostic_reminder_2h"

DIAGNOSTIC_CTA_CALLBACKS = {
    "diagnostic_cta_anxiety",
    "diagnostic_cta_tired",
    "diagnostic_cta_procrastination",
    "diagnostic_cta_unclear",
}

DIAGNOSTIC_SIGNUP_CALLBACKS = {
    "diagnostic_signup",
    "diagnostic_signup_20m",
    "diagnostic_signup_2h",
}

BOOKING_INTRO_TEXT = (
    "Бесплатный разбор с нашим специалистом длится около 20 минут.\n"
    "Это не жёсткая консультация и не разговор в стиле «что с вами не так». "
    "С вами свяжется Служба заботы — специалисты и психологи, которые помогут спокойно увидеть, "
    "где тело сжимается рядом с ростом, деньгами и новым уровнем.\n\n"
    "На разборе вы не будете говорить “вообще про жизнь”. Вы возьмёте конкретную денежную цель "
    "и посмотрите, где рядом с ней есть расширение, а где появляется тревога, давление, усталость, "
    "откладывание или страх не справиться.\n\n"
    "Сейчас бот покажет следующий шаг, чтобы специалист мог согласовать с вами удобное время."
)

REMINDER_20M_TEXT = (
    "Вы уже сделали первый шаг — заметили своё состояние. Но мини-тест показывает только направление, "
    "а разбор помогает увидеть вашу конкретную точку.\n\n"
    "Например, человек может выбрать «хочу, но тревожно». Но тревога у одного связана со страхом "
    "не удержать деньги, у другого — с ответственностью, у третьего — с проявленностью, "
    "у четвёртого — с хаосом и непредсказуемостью.\n\n"
    "Именно поэтому важен разбор. На нём специалист помогает увидеть не общую боль, а вашу личную "
    "связку: какая цель есть, что вы хотите, где тело сжимается и почему движение к деньгам может "
    "тормозиться.\n\n"
    "Если хотите не просто прочитать про это, а понять, как это устроено именно у вас, "
    "приходите на бесплатный разбор «Выдерживает ли твоё тело деньги»."
)

REMINDER_2H_TEXT = (
    "Возможно, вы пока не нажали на разбор, потому что внутри есть вопрос: «А что там будет?»\n\n"
    "Коротко: это 20 минут, где специалист помогает вам увидеть вашу точку. Не заставляет делать "
    "больше, не оценивает, не говорит, что с вами что-то не так, а спокойно разбирает, где вы хотите "
    "роста, а где тело сжимается, устаёт, тревожится или откладывает.\n\n"
    "Иногда человеку кажется, что ему нужна новая стратегия. А на самом деле сначала нужно понять, "
    "почему рядом с деньгами включается напряжение.\n\n"
    "Разбор нужен именно для этого: увидеть, что сейчас мешает вам идти к большему — отсутствие действий "
    "или состояние, которое ваша система пока не выдерживает."
)


class DiagnosticLeadState(StatesGroup):
    waiting_for_name = State()
    waiting_for_phone = State()
    waiting_for_confirmation = State()


def get_asyncpg_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1) if dsn else ""


def build_inline_buttons_payload(rows: list[list[dict]]) -> str:
    return json.dumps({"inline_keyboard": rows}, ensure_ascii=False)


def build_single_button_keyboard(text: str, callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=callback_data)]]
    )


async def mark_user_event(user_id: int, scenario_type: str, text_content: str):
    if not DB_DSN:
        raise RuntimeError("DB_DSN is not set")

    conn = await asyncpg.connect(get_asyncpg_dsn(DB_DSN))
    try:
        await conn.execute(
            """
            INSERT INTO scheduled_messages
                (user_id, scenario_type, text_content, send_at, status)
            VALUES
                ($1, $2, $3, NOW(), 'sent');
            """,
            user_id,
            scenario_type,
            text_content,
        )
    finally:
        await conn.close()


async def cancel_pending_diagnostic_reminders(user_id: int):
    if not DB_DSN:
        raise RuntimeError("DB_DSN is not set")

    conn = await asyncpg.connect(get_asyncpg_dsn(DB_DSN))
    try:
        await conn.execute(
            """
            UPDATE scheduled_messages
            SET status = 'cancelled'
            WHERE user_id = $1
              AND scenario_type = ANY($2::text[])
              AND status = 'pending';
            """,
            user_id,
            [DIAGNOSTIC_REMINDER_20M_SCENARIO, DIAGNOSTIC_REMINDER_2H_SCENARIO],
        )
    finally:
        await conn.close()


async def schedule_diagnostic_reminders(user_id: int):
    if not DB_DSN:
        raise RuntimeError("DB_DSN is not set")

    await cancel_pending_diagnostic_reminders(user_id)

    conn = await asyncpg.connect(get_asyncpg_dsn(DB_DSN))
    try:
        await conn.execute(
            """
            INSERT INTO scheduled_messages
                (user_id, scenario_type, text_content, inline_buttons, send_at)
            VALUES
                ($1, $2, $3, $4::jsonb, NOW() + INTERVAL '20 minutes');
            """,
            user_id,
            DIAGNOSTIC_REMINDER_20M_SCENARIO,
            REMINDER_20M_TEXT,
            build_inline_buttons_payload(
                [[{"text": "Пройти разбор", "callback_data": "diagnostic_signup_20m"}]]
            ),
        )

        await conn.execute(
            """
            INSERT INTO scheduled_messages
                (user_id, scenario_type, text_content, inline_buttons, send_at)
            VALUES
                ($1, $2, $3, $4::jsonb, NOW() + INTERVAL '2 hours');
            """,
            user_id,
            DIAGNOSTIC_REMINDER_2H_SCENARIO,
            REMINDER_2H_TEXT,
            build_inline_buttons_payload(
                [[{"text": "Хочу 20-минутный разбор", "callback_data": "diagnostic_signup_2h"}]]
            ),
        )
    finally:
        await conn.close()


@diagnostic_router.callback_query(F.data.in_(DIAGNOSTIC_CTA_CALLBACKS))
async def handle_diagnostic_cta(callback: CallbackQuery):
    await callback.answer()
    if not callback.message:
        return

    user_id = callback.from_user.id
    try:
        await mark_user_event(user_id, "diagnostic_cta_clicked", callback.data)
    except Exception as exc:
        print(f"❌ Не удалось отметить diagnostic_cta_clicked: {exc}")

    await callback.message.answer(
        BOOKING_INTRO_TEXT,
        reply_markup=build_single_button_keyboard(
            "Записаться на разбор", "diagnostic_signup"
        ),
    )


@diagnostic_router.callback_query(F.data.in_(DIAGNOSTIC_SIGNUP_CALLBACKS))
async def start_diagnostic_signup(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not callback.message:
        return

    user_id = callback.from_user.id
    try:
        await cancel_pending_diagnostic_reminders(user_id)
        await mark_user_event(user_id, "diagnostic_signup_started", callback.data)
    except Exception as exc:
        print(f"❌ Не удалось обновить состояния перед сбором заявки: {exc}")

    await state.update_data(signup_source=callback.data)
    await callback.message.answer("Напишите, пожалуйста, как к вам обращаться (имя):")
    await state.set_state(DiagnosticLeadState.waiting_for_name)


@diagnostic_router.message(DiagnosticLeadState.waiting_for_name)
async def process_diagnostic_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("Пожалуйста, напишите имя текстом.")
        return

    await state.update_data(name=name)
    await message.answer("Теперь напишите ваш номер телефона:")
    await state.set_state(DiagnosticLeadState.waiting_for_phone)


@diagnostic_router.message(DiagnosticLeadState.waiting_for_phone)
async def process_diagnostic_phone(message: Message, state: FSMContext):
    phone = (message.text or "").strip()
    if not phone:
        await message.answer("Пожалуйста, введите номер телефона текстом.")
        return

    await state.update_data(phone=phone)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Данные введены правильно",
                    callback_data="diagnostic_phone_confirm",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Исправить номер",
                    callback_data="diagnostic_phone_edit",
                )
            ],
        ]
    )
    await message.answer(f"Вы ввели номер: <b>{phone}</b>\n\nВсё верно?", reply_markup=kb)
    await state.set_state(DiagnosticLeadState.waiting_for_confirmation)


@diagnostic_router.callback_query(
    F.data == "diagnostic_phone_edit",
    DiagnosticLeadState.waiting_for_confirmation,
)
async def edit_diagnostic_phone(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not callback.message:
        return

    await callback.message.edit_text("Введите номер телефона еще раз:")
    await state.set_state(DiagnosticLeadState.waiting_for_phone)


@diagnostic_router.callback_query(
    F.data == "diagnostic_phone_confirm",
    DiagnosticLeadState.waiting_for_confirmation,
)
async def confirm_diagnostic_signup(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not callback.message:
        return

    user_data = await state.get_data()
    name = user_data.get("name", "")
    phone = user_data.get("phone", "")
    signup_source = user_data.get("signup_source", "diagnostic_signup")
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
    finally:
        await conn.close()

    try:
        await cancel_pending_diagnostic_reminders(user_id)
        await mark_user_event(
            user_id,
            "diagnostic_signup_completed",
            json.dumps(
                {"source": signup_source, "name": name, "phone": phone},
                ensure_ascii=False,
            ),
        )
    except Exception as exc:
        print(f"❌ Не удалось сохранить итоговую отметку заявки: {exc}")

    webhook_url = "https://superegocomp.app.n8n.cloud/webhook/bot3"
    payload = {
        "user_id": user_id,
        "username": username,
        "name": name,
        "phone": phone,
        "action": "diagnostic_signup",
        "source": signup_source,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as response:
                print(f"n8n webhook status: {response.status}")
    except Exception as exc:
        print(f"❌ Ошибка отправки вебхука в n8n: {exc}")

    await callback.message.answer(
        "✅ <b>Спасибо!</b> Заявка принята. Служба заботы свяжется с вами, "
        "чтобы согласовать удобное время разбора."
    )
    await state.clear()
