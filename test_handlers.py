import os
import asyncpg
import aiohttp
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv

load_dotenv()
DB_DSN = os.getenv("DB_DSN")

def get_asyncpg_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+asyncpg://", "postgresql://", 1) if dsn else ""

test_router = Router()

# ==========================================
# МАШИНА СОСТОЯНИЙ (FSM) ДЛЯ НОМЕРА ТЕЛЕФОНА
# ==========================================
class PhoneState(StatesGroup):
    waiting_for_phone = State()
    waiting_for_confirmation = State()

# ==========================================
# 1. СЛОВАРЬ ВОПРОСОВ И ПЕРЕХОДОВ
# ==========================================
QUESTIONS = {
    1: {"text": "<b>1.</b> Приходит зарплата, и уже через неделю вы ловите себя на мысли, что не помните, куда ушли деньги, и начинаете экономить до следующей выплаты.", "category": "finance_score"},
    2: {"text": "<b>2.</b> Друзья зовут в ресторан, на концерт или в спонтанное путешествие, и вы отказываетесь не потому, что не хотите, а потому, что не вписываетесь в бюджет.", "category": "finance_score"},
    3: {"text": "<b>3.</b> Вы мечтаете о крупной покупке (квартира, машина, обучение, свой бизнес), но не представляете, как накопить на неё, и откладывание «на потом» длится годами.", "category": "finance_score"},
    4: {"text": "<b>4.</b> Вы замечали, что покупаете ненужные вещи под влиянием эмоций, распродаж или чужого мнения, а потом жалеете и чувствуете вину.", "category": "finance_score"},
    
    5: {"text": "<b>5.</b> На работе вы часто ловите себя на мысли: «Зачем я это делаю? Какой в этом смысл для меня лично?»", "category": "purpose_score"},
    6: {"text": "<b>6.</b> Когда вас спрашивают «Чем ты увлекаешься?» или «В чём твоё призвание?», вы не можете дать чёткого ответа.", "category": "purpose_score"},
    7: {"text": "<b>7.</b> Вы завидуете людям, которые горят своим делом, работают с энтузиазмом или нашли своё «то самое», потому что сами не испытываете подобного огня.", "category": "purpose_score"},
    8: {"text": "<b>8.</b> У вас есть смутное ощущение, что вы могли бы реализовать что-то гораздо большее, но вы не знаете, с чего начать, и боитесь, что так и проживёте, не раскрыв свой потенциал.", "category": "purpose_score"},
    
    9: {"text": "<b>9.</b> В конфликте с близким человеком вам трудно отстаивать свои границы – вы чаще соглашаетесь, проглатываете обиду или молчите, лишь бы не ссориться.", "category": "relations_score"},
    10: {"text": "<b>10.</b> Вы чувствуете, что вкладываетесь в отношения (дружеские, романтические, семейные) больше, чем получаете взамен: поддержку, внимание, заботу.", "category": "relations_score"},
    11: {"text": "<b>11.</b> Вам трудно попросить о помощи или поддержке, даже когда вы вымотаны или в тупике, – легче справиться самому, чем открыться другому.", "category": "relations_score"},
    12: {"text": "<b>12.</b> Вы часто ощущаете одиночество, даже когда вокруг есть люди, – словно вас не слышат, не понимают или вы не можете показать себя настоящего.", "category": "relations_score"},
    
    13: {"text": "<b>13.</b> Ваш типичный выходной или вечер после работы выглядит одинаково из месяца в месяц (соцсети, сериал, еда, сон), и вас это уже наскучило, но сил или идей что-то менять нет.", "category": "life_score"},
    14: {"text": "<b>14.</b> Вы не можете вспомнить, когда в последний раз делали что-то новое, что вызвало бы настоящий восторг, смех до слез или трепет как в детстве.", "category": "life_score"},
    15: {"text": "<b>15.</b> Вы постоянно откладываете маленькие радости на потом (купить цветы, устроить пикник, сходить в кино одному, приготовить красивое блюдо), потому что «некогда», «не по средствам» или «сначала дела».", "category": "life_score"},
    16: {"text": "<b>16.</b> Оглядываясь на последний год, вы видите в основном серые будни, рутину и мало ярких событий, о которых можно было бы вспомнить с улыбкой, – будто жизнь проходит на автопилоте.", "category": "life_score"},
}

TRANSITIONS = {
    5: "<b>ПРЕДНАЗНАЧЕНИЕ (смысловой затык)</b>\n\nВ конце этого блока посчитайте, сколько у вас получилось баллов по теме предназначения.",
    9: "<b>ОТНОШЕНИЯ (коммуникативный затык)</b>\n\nВ конце этого блока посчитайте, сколько у вас получилось баллов по теме отношений.",
    13: "<b>ЯРКОСТЬ ЖИЗНИ (эмоциональный затык)</b>\n\nВ конце этого блока посчитайте, сколько у вас получилось баллов по теме яркость жизни."
}

# ==========================================
# 2. ФУНКЦИЯ СОЗДАНИЯ КЛАВИАТУРЫ ДЛЯ ВОПРОСА
# ==========================================
def get_question_keyboard(question_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="А — почти никогда (1 балл)", callback_data=f"ans_{question_id}_1")],
        [InlineKeyboardButton(text="Б — редко (2 балла)", callback_data=f"ans_{question_id}_2")],
        [InlineKeyboardButton(text="В — иногда (3 балла)", callback_data=f"ans_{question_id}_3")],
        [InlineKeyboardButton(text="Г — часто (4 балла)", callback_data=f"ans_{question_id}_4")],
        [InlineKeyboardButton(text="Д — почти всегда (5 баллов)", callback_data=f"ans_{question_id}_5")]
    ])

# ==========================================
# 3. СТАРТ ТЕСТА (С обнулением старых баллов)
# ==========================================
@test_router.callback_query(F.data == "start_test")
async def start_quiz(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear() # На всякий случай сбрасываем любые зависшие состояния
    
    user_id = callback.from_user.id
    
    conn = await asyncpg.connect(get_asyncpg_dsn(DB_DSN))
    try:
        # Если юзер проходит тест заново — обнуляем все его баллы!
        await conn.execute("""
            INSERT INTO test_results_3db (user_id, finance_score, purpose_score, relations_score, life_score) 
            VALUES ($1, 0, 0, 0, 0) 
            ON CONFLICT (user_id) DO UPDATE 
            SET finance_score = 0, purpose_score = 0, relations_score = 0, life_score = 0;
        """, user_id)
    finally:
        await conn.close()
    
    await callback.message.answer(QUESTIONS[1]["text"], reply_markup=get_question_keyboard(1))

# ==========================================
# 4. ОБРАБОТКА ОТВЕТОВ НА ВОПРОСЫ
# ==========================================
@test_router.callback_query(F.data.startswith("ans_"))
async def process_test_answer(callback: CallbackQuery):
    await callback.answer()
    
    _, current_q_str, score_str = callback.data.split('_')
    current_q = int(current_q_str)
    score = int(score_str)
    user_id = callback.from_user.id
    
    category_column = QUESTIONS[current_q]["category"]
    
    conn = await asyncpg.connect(get_asyncpg_dsn(DB_DSN))
    try:
        await conn.execute(f"""
            UPDATE test_results_3db 
            SET {category_column} = COALESCE({category_column}, 0) + $1 
            WHERE user_id = $2;
        """, score, user_id)
    finally:
        await conn.close()
    
    await callback.message.edit_text(
        f"{QUESTIONS[current_q]['text']}\n\n<i>Ваш ответ: {score} балл(а/ов)</i>"
    )
    
    next_q = current_q + 1
    
    if next_q <= 16:
        if next_q in TRANSITIONS:
            await callback.message.answer(TRANSITIONS[next_q])
            
        await callback.message.answer(QUESTIONS[next_q]["text"], reply_markup=get_question_keyboard(next_q))
    else:
        final_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="ОСТАВИТЬ НОМЕР 📱", callback_data="get_phone")]
        ])
        await callback.message.answer(
            "Вы почти закончили! 🎉\n\n"
            "Чтобы получить подробную расшифровку результата по каждому затыку, оставьте свой номер телефона в форме внизу 👇🏼",
            reply_markup=final_keyboard
        )

# ==========================================
# 5. FSM: СБОР НОМЕРА И ПОКАЗ РЕЗУЛЬТАТОВ
# ==========================================
@test_router.callback_query(F.data == "get_phone")
async def ask_for_phone(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Пожалуйста, напишите ваш номер телефона (например, +79991234567):")
    await state.set_state(PhoneState.waiting_for_phone)

@test_router.message(PhoneState.waiting_for_phone)
async def process_phone_input(message: Message, state: FSMContext):
    phone = message.text
    await state.update_data(phone=phone)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Номер введен правильно", callback_data="phone_correct")],
        [InlineKeyboardButton(text="✏️ Исправить номер", callback_data="phone_edit")]
    ])
    
    await message.answer(f"Вы ввели номер: <b>{phone}</b>\n\nВсё верно?", reply_markup=kb)
    await state.set_state(PhoneState.waiting_for_confirmation)

@test_router.callback_query(F.data == "phone_edit", PhoneState.waiting_for_confirmation)
async def edit_phone(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text("Пожалуйста, введите ваш номер телефона еще раз:")
    await state.set_state(PhoneState.waiting_for_phone)

@test_router.callback_query(F.data == "phone_correct", PhoneState.waiting_for_confirmation)
async def confirm_phone(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    
    user_data = await state.get_data()
    phone = user_data.get("phone")
    user_id = callback.from_user.id
    username = callback.from_user.username
    
    conn = await asyncpg.connect(get_asyncpg_dsn(DB_DSN))
    try:
        # Пишем телефон в общую таблицу
        await conn.execute("""
            UPDATE users_3db 
            SET phone = $1 
            WHERE user_id = $2;
        """, phone, user_id)
        
        # Читаем баллы из таблицы результатов
        scores_row = await conn.fetchrow("""
            SELECT finance_score, purpose_score, relations_score, life_score 
            FROM test_results_3db 
            WHERE user_id = $1;
        """, user_id)
    finally:
        await conn.close()
        
    # Отправляем вебхук в n8n
    webhook_url = "https://superegocomp.app.n8n.cloud/webhook/bot3"
    payload = {
        "user_id": user_id,
        "username": username,
        "phone": phone,
        "finance_score": scores_row['finance_score'],
        "purpose_score": scores_row['purpose_score'],
        "relations_score": scores_row['relations_score'],
        "life_score": scores_row['life_score'],
        "action": "test_completed"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as response:
                print(f"n8n webhook status: {response.status}")
    except Exception as e:
        print(f"❌ Ошибка отправки вебхука в n8n: {e}")

    # ФОРМИРУЕМ КРАСИВУЮ РАСШИФРОВКУ
    def get_status_text(score):
        if score <= 8:
            return "🟢 <i>Всё спокойно или почти спокойно</i>"
        elif score <= 13:
            return "🟡 <i>Есть моменты, которые стоит не игнорировать</i>"
        else:
            return "🔴 <b>Здесь сейчас больше всего напряжения</b>"

    f_score = scores_row['finance_score']
    p_score = scores_row['purpose_score']
    r_score = scores_row['relations_score']
    l_score = scores_row['life_score']

    categories = {
        "Деньги": f_score,
        "Предназначение": p_score,
        "Отношения": r_score,
        "Яркость жизни": l_score
    }
    max_score = max(categories.values())
    problem_cats = [name for name, score in categories.items() if score == max_score]
    
    if len(problem_cats) == 1:
        conclusion = f"Сфера <b>«{problem_cats[0]}»</b> набрала больше всего баллов. Она сейчас сильнее всего влияет на ваше общее состояние."
    else:
        cats_str = ", ".join([f"<b>«{c}»</b>" for c in problem_cats])
        conclusion = f"Сферы {cats_str} набрали больше всего баллов. Значит, напряжение проявляется сразу в нескольких частях жизни."

    result_text = (
        "✅ <b>Спасибо! Номер успешно сохранен.</b>\n\n"
        "А вот и расшифровка ваших результатов теста:\n\n"
        f"💰 <b>Деньги: {f_score} баллов</b>\n{get_status_text(f_score)}\n\n"
        f"🎯 <b>Предназначение: {p_score} баллов</b>\n{get_status_text(p_score)}\n\n"
        f"🤝 <b>Отношения: {r_score} баллов</b>\n{get_status_text(r_score)}\n\n"
        f"🌟 <b>Яркость жизни: {l_score} баллов</b>\n{get_status_text(l_score)}\n\n"
        "〰️〰️〰️〰️〰️\n"
        f"<b>Главный вывод:</b>\n{conclusion}"
    )

    kb_balance = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Как выровнять все сферы жизни? ⚖️", callback_data="how_to_balance")]
    ])

    await callback.message.edit_text(result_text, reply_markup=kb_balance)
    await state.clear()

# ==========================================
# 6. КНОПКА "Как выровнять все сферы жизни?"
# ==========================================
@test_router.callback_query(F.data == "how_to_balance")
async def process_how_to_balance(callback: CallbackQuery):
    await callback.answer()
    
    await callback.message.answer(
        "<b>Здесь будет полезная информация и следующий шаг воронки! 🚀</b>\n\n"
        "Скоро мы расскажем, как прийти к балансу во всех этих сферах."
    )