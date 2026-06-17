import json
import os

import asyncpg
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

from diagnostic_handlers import mark_user_event, schedule_diagnostic_reminders

load_dotenv()
DB_DSN = os.getenv("DB_DSN")


def get_asyncpg_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1) if dsn else ""


book_followup_router = Router()

BOOK_FOLLOWUP_SCENARIO = "book_followup_20m"
BOOK_FOLLOWUP_TEXT = (
    "<b>Предлагаем вам пройти короткий мини-тест.</b> Он нужен не для того, чтобы поставить вам диагноз или оценить, \"правильно\" вы относитесь к деньгам.\n\n"
    "Его задача — помочь вам быстро увидеть, из какого состояния вы сейчас идёте к росту: из интереса, энергии и желания большего или из напряжения, тревоги, усталости и внутреннего «надо».\n\n"
    "Иногда человек думает, что ему просто нужна новая стратегия, больше дисциплины или мотивации. А на самом деле тело уже показывает: рядом с деньгами есть сжатие, и именно оно может тормозить проявленность, решения, продажи, рост дохода или способность удерживать результат.\n\n"
    "Мини-тест займёт меньше минуты и покажет, с чего у вас может начинаться это сжатие: с тревоги, усталости, откладывания или потери ясности. После ответа вы получите короткий вывод и сможете пройти бесплатный разбор «Выдерживает ли твоё тело деньги», чтобы посмотреть на свою ситуацию уже глубже.\n\n"
    "Когда вы думаете о деньгах, росте и новом уровне жизни, что ближе всего?"
)

FOLLOWUP_OPTIONS = [
    ("Хочу больше, но внутри тревожно", "book_followup_anxiety"),
    ("Хочу больше, но нет сил", "book_followup_tired"),
    ("Хочу больше, но всё откладываю", "book_followup_procrastination"),
    ("Уже не понимаю, чего хочу", "book_followup_unclear"),
]

FOLLOWUP_CHOICE_RESPONSES = {
    "book_followup_anxiety": {
        "text": (
            "Тревога рядом с деньгами часто говорит не о слабости. Иногда тело просто не чувствует безопасности в новом уровне. "
            "Голова может хотеть большего, но внутри появляется: «а если я не справлюсь?», «а если станет слишком много?», "
            "«а если я не удержу результат?».\n\n"
            "И тогда деньги ощущаются не как свобода, а как нагрузка. Сложнее проявляться, продавать, брать больше и принимать решения, "
            "потому что тело считывает рост как опасность.\n\n"
            "На бесплатном разборе «Выдерживает ли твоё тело деньги» специалист поможет увидеть, где именно у вас включается эта тревога "
            "и как она может влиять на деньги."
        ),
        "button_text": "Разобрать мою тревогу",
        "button_callback": "diagnostic_cta_anxiety",
        "state_marker": "anxiety",
    },
    "book_followup_tired": {
        "text": (
            "Когда хочется большего, но нет сил, это не всегда про лень. Иногда тело слишком долго жило в режиме «надо», «соберись», "
            "«ещё чуть-чуть», и рост начинает ощущаться не как свобода, а как ещё одна нагрузка.\n\n"
            "В таком состоянии деньги тоже могут идти тяжело. Не хватает энергии на проявленность, решения, продажи, действия и удержание "
            "результата. Человек вроде хочет больше, но внутри как будто нет ресурса это выдержать.\n\n"
            "На бесплатном разборе «Выдерживает ли твоё тело деньги» специалист поможет увидеть, где ваша система перегружена "
            "и почему движение к деньгам может тормозиться."
        ),
        "button_text": "Разобрать, почему нет сил",
        "button_callback": "diagnostic_cta_tired",
        "state_marker": "no_energy",
    },
    "book_followup_procrastination": {
        "text": (
            "Откладывание не всегда означает, что вы несобранная или недостаточно мотивированная. Иногда это защита: тело чувствует, "
            "что следующий шаг слишком большой, слишком заметный или слишком ответственный.\n\n"
            "Особенно если шаг связан с деньгами: заявить о себе, поднять цену, продать, принять решение, выйти на новый уровень. "
            "Голова может хотеть, а тело — тормозить.\n\n"
            "На бесплатном разборе «Выдерживает ли твоё тело деньги» можно увидеть, что именно стоит за вашим откладыванием: усталость, "
            "страх, сжатие перед новым уровнем или состояние, которое тело пока не выдерживает."
        ),
        "button_text": "Понять, почему откладываю",
        "button_callback": "diagnostic_cta_procrastination",
        "state_marker": "procrastination",
    },
    "book_followup_unclear": {
        "text": (
            "Когда человек долго живёт в напряжении, желания могут приглушаться. Внешне всё может быть нормально: дела, задачи, планы, "
            "привычный ритм. Но внутри появляется скука, автопилот и ощущение: «я вроде живу, но не чувствую движения».\n\n"
            "Это тоже влияет на деньги. Когда внутри мало ясности и желания, сложнее выбирать, проявляться, брать больше, создавать "
            "и удерживать новый уровень.\n\n"
            "На бесплатном разборе «Выдерживает ли твоё тело деньги» специалист поможет мягко посмотреть, где внутри погасло движение "
            "и почему тело может не пускать вас в большее."
        ),
        "button_text": "Разобрать моё состояние",
        "button_callback": "diagnostic_cta_unclear",
        "state_marker": "unclear_state",
    },
}


def build_followup_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=callback_data)] for text, callback_data in FOLLOWUP_OPTIONS]
    )


def build_followup_buttons_payload() -> str:
    payload = {
        "inline_keyboard": [
            [{"text": text, "callback_data": callback_data}]
            for text, callback_data in FOLLOWUP_OPTIONS
        ]
    }
    return json.dumps(payload, ensure_ascii=False)


async def schedule_book_followup(user_id: int):
    if not DB_DSN:
        raise RuntimeError("DB_DSN is not set")

    conn = await asyncpg.connect(get_asyncpg_dsn(DB_DSN))
    try:
        await conn.execute(
            """
            UPDATE scheduled_messages
            SET status = 'cancelled'
            WHERE user_id = $1
              AND scenario_type = $2
              AND status = 'pending';
            """,
            user_id,
            BOOK_FOLLOWUP_SCENARIO,
        )

        await conn.execute(
            """
            INSERT INTO scheduled_messages
                (user_id, scenario_type, text_content, inline_buttons, send_at)
            VALUES
                ($1, $2, $3, $4::jsonb, NOW() + INTERVAL '20 minutes');
            """,
            user_id,
            BOOK_FOLLOWUP_SCENARIO,
            BOOK_FOLLOWUP_TEXT,
            build_followup_buttons_payload(),
        )
    finally:
        await conn.close()


async def cancel_pending_book_followup(user_id: int):
    if not DB_DSN:
        raise RuntimeError("DB_DSN is not set")

    conn = await asyncpg.connect(get_asyncpg_dsn(DB_DSN))
    try:
        await conn.execute(
            """
            UPDATE scheduled_messages
            SET status = 'cancelled'
            WHERE user_id = $1
              AND scenario_type = $2
              AND status = 'pending';
            """,
            user_id,
            BOOK_FOLLOWUP_SCENARIO,
        )
    finally:
        await conn.close()


async def send_book_followup_prompt(message: Message):
    await message.answer(BOOK_FOLLOWUP_TEXT, reply_markup=build_followup_keyboard())


@book_followup_router.callback_query(F.data == "start_book_followup_test")
async def start_book_followup_from_button(callback: CallbackQuery):
    await callback.answer()
    if not callback.message:
        return
    await send_book_followup_prompt(callback.message)


@book_followup_router.callback_query(F.data.in_({option[1] for option in FOLLOWUP_OPTIONS}))
async def handle_followup_choice(callback: CallbackQuery):
    await callback.answer()
    if not callback.message:
        return

    user_id = callback.from_user.id
    selected = FOLLOWUP_CHOICE_RESPONSES.get(callback.data)
    if not selected:
        return

    try:
        await mark_user_event(user_id, "book_followup_choice", selected["state_marker"])
        await schedule_diagnostic_reminders(user_id)
    except Exception as exc:
        print(f"❌ Не удалось сохранить состояние follow-up: {exc}")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=selected["button_text"],
                    callback_data=selected["button_callback"],
                )
            ]
        ]
    )
    await callback.message.answer(selected["text"], reply_markup=kb)

