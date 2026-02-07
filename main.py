import asyncio
import logging
import sys
import random
import json
import re
import os
import tempfile
from aiogram import Bot, Dispatcher, html, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, PollType
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, PollAnswer, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from openai import AsyncOpenAI
from config import BOT_TOKEN, DEEPSEEK_API_KEY

# PDF and DOCX support
try:
    from PyPDF2 import PdfReader
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

try:
    from docx import Document
    DOCX_SUPPORT = True
except ImportError:
    DOCX_SUPPORT = False

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize bot and dispatcher
dp = Dispatcher()
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# Initialize DeepSeek client
deepseek_client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com/v1"
)

# User data storage
user_data = {}

# Quiz States
class QuizStates(StatesGroup):
    selecting_subject = State()
    selecting_time = State()
    generating = State()
    answering = State()
    entering_topic = State()
    topic_selecting_time = State()
    waiting_file = State()
    file_selecting_time = State()

# Keyboards
age_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="👶 10-18"), KeyboardButton(text="👨 18-25"), KeyboardButton(text="👴 25-35")]],
    resize_keyboard=True
)

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Quiz yaratish"), KeyboardButton(text="🚀 Quiz boshlash")],
        [KeyboardButton(text="🗣️ Murojatlar"), KeyboardButton(text="ℹ️ Yordam")]
    ],
    resize_keyboard=True
)

subject_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📐 Matematika"), KeyboardButton(text="📖 Ona tili")],
        [KeyboardButton(text="🏛️ Tarix"), KeyboardButton(text="🇬🇧 Ingliz tili")],
        [KeyboardButton(text="⚡ Fizika")],
        [KeyboardButton(text="⬅️ Ortga")]
    ],
    resize_keyboard=True
)

time_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⏱️ 30 soniya"), KeyboardButton(text="⏱️ 1 daqiqa"), KeyboardButton(text="⏱️ 3 daqiqa")],
        [KeyboardButton(text="⬅️ Ortga")]
    ],
    resize_keyboard=True
)

quiz_creation_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📚 Mavzu tanlash"), KeyboardButton(text="📁 Fayl tanlash")],
        [KeyboardButton(text="⬅️ Ortga")]
    ],
    resize_keyboard=True
)

cancel_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
    resize_keyboard=True
)

quiz_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🛑 Quizni tugatish")]],
    resize_keyboard=True
)

# ============ FILE TEXT EXTRACTION ============

async def extract_text_from_pdf(file_path: str) -> str:
    if not PDF_SUPPORT:
        return ""
    try:
        reader = PdfReader(file_path)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text
    except Exception as e:
        logging.error(f"PDF extraction error: {e}")
        return ""

async def extract_text_from_docx(file_path: str) -> str:
    if not DOCX_SUPPORT:
        return ""
    try:
        doc = Document(file_path)
        text = "\n".join([para.text for para in doc.paragraphs])
        return text
    except Exception as e:
        logging.error(f"DOCX extraction error: {e}")
        return ""

async def extract_text_from_txt(file_path: str) -> str:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except:
        try:
            with open(file_path, 'r', encoding='cp1251') as f:
                return f.read()
        except Exception as e:
            logging.error(f"TXT extraction error: {e}")
            return ""

# ============ PARSE QUESTIONS FROM FILE ============

def parse_questions_from_text(text: str) -> list:
    """
    Parse questions and answers from text.
    Supported formats:
    
    Format 1:
    1. Savol matni?
    A) Javob 1
    B) Javob 2
    C) Javob 3
    D) Javob 4
    Javob: A
    
    Format 2:
    Savol: Savol matni?
    A) Javob 1
    B) Javob 2
    C) Javob 3
    D) Javob 4
    To'g'ri javob: A
    
    Format 3:
    1. Savol matni?
    a) Javob 1
    b) Javob 2 *
    c) Javob 3
    (* marks correct answer)
    """
    questions = []
    
    # Split text into blocks (by double newline or numbered questions)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # Pattern for questions with explicit answer indicator
    # Match: number or "Savol:", then question text, then options A/B/C/D, then answer line
    question_pattern = re.compile(
        r'(?:(\d+)[.\)]\s*|Savol:\s*)(.*?)\n'  # Question number/prefix and text
        r'([AaBb][.\)]\s*.+?\n'  # Option A/B
        r'[BbCc][.\)]\s*.+?\n'   # Option B/C
        r'(?:[CcDd][.\)]\s*.+?\n)?'  # Option C/D (optional)
        r'(?:[DdEe][.\)]\s*.+?\n)?'  # Option D/E (optional)
        r')',
        re.DOTALL
    )
    
    # Split by common question separators
    blocks = re.split(r'\n\s*\n+', text)
    
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        
        lines = [l.strip() for l in block.split('\n') if l.strip()]
        if len(lines) < 3:
            continue
        
        question_text = None
        options = []
        correct_idx = 0
        
        # Find question text (first line or line starting with number/Savol)
        start_idx = 0
        for i, line in enumerate(lines):
            # Check if line is a question
            q_match = re.match(r'^(?:\d+[.\)]\s*|Savol:\s*)(.*)', line, re.IGNORECASE)
            if q_match:
                question_text = q_match.group(1).strip()
                if not question_text and i + 1 < len(lines):
                    question_text = lines[i + 1]
                    start_idx = i + 2
                else:
                    start_idx = i + 1
                break
        
        if not question_text:
            # First line is question
            question_text = lines[0]
            # Remove leading number if present
            question_text = re.sub(r'^\d+[.\)]\s*', '', question_text)
            start_idx = 1
        
        # Parse options
        for i in range(start_idx, len(lines)):
            line = lines[i]
            
            # Check for answer line (Latin A-D or Cyrillic А-Д)
            answer_match = re.match(r'^(?:Javob|To\'g\'ri javob|Answer|Correct)[:\s]*([A-DА-Д])', line, re.IGNORECASE)
            if answer_match:
                letter = answer_match.group(1).upper()
                # Map Cyrillic to Latin index
                mapping = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'А': 0, 'Б': 1, 'С': 2, 'Д': 3}
                correct_idx = mapping.get(letter, 0)
                continue
            
            # Check for option line (A), B), a., etc. supports both Latin and Cyrillic)
            option_match = re.match(r'^([A-DА-Дa-dа-д])[.\)]\s*(.+)', line)
            if option_match:
                option_text = option_match.group(2).strip()
                
                # Check if this option is marked as correct (with *, +, (✓), (v))
                if any(option_text.endswith(m) for m in ['*', '+', '(✓)', '(v)']):
                    correct_idx = len(options)
                    # Remove the marker
                    for m in ['*', '+', '(✓)', '(v)']:
                        if option_text.endswith(m):
                            option_text = option_text[:-len(m)].strip()
                            break
                
                options.append(option_text)
        
        # Validate we have enough data
        if question_text and len(options) >= 2:
            # Ensure correct_idx is valid
            correct_idx = min(correct_idx, len(options) - 1)
            
            questions.append({
                "question": question_text,
                "options": options,
                "correct": correct_idx
            })
    
    return questions

def shuffle_options(questions: list) -> list:
    """Shuffle options for each question while tracking correct answer"""
    shuffled_questions = []
    
    for q in questions:
        options = q["options"][:]
        correct_option = options[q["correct"]]
        
        # Shuffle options
        random.shuffle(options)
        
        # Find new index of correct answer
        new_correct_idx = options.index(correct_option)
        
        shuffled_questions.append({
            "question": q["question"],
            "options": options,
            "correct": new_correct_idx
        })
    
    return shuffled_questions

# ============ DEEPSEEK GENERATION ============

async def generate_questions_from_text(text: str, age: str, count: int = 100) -> list:
    """Ask DeepSeek to parse or create questions from provided text"""
    difficulty = {"10-18": "oson", "18-25": "o'rtacha", "25-35": "qiyin"}.get(age, "o'rtacha")
    
    prompt = f"""Sen professionat quiz generatorisan. Quyidagi matnni tahlil qil va imkon qadar ko'p (maksimal {count} ta) savoldan iborat quiz yarat.

MUHIM TALAblar: 
1. Matn lotin yoki kirill alifbosida bo'lishi mumkin (O'zbekcha yoki ruscha).
2. Agar matnda faqat savollar bo'lsa, ularga to'g'ri javoblarni o'zing top va 4 ta variantli (A, B, C, D) quiz ko'rinishiga keltir.
3. Agar matnda savol-javoblar bo'lsa, ularni tahlil qil va JSON formatiga o'gir. Agar biror savolda javob variantlari kam bo'lsa, ularni to'ldir.
4. Agar matn umumiy ma'lumot (maqola, darslik) bo'lsa, shu matndan kelib chiqib qiziqarli savollar yarat.
5. Har bir savol uchun HAR DOIM 4 ta variant bo'lishi shart.
6. Qiyinlik darajasi: {difficulty}
7. Faqat JSON formatda javob ber.

MATN:
{text[:6000]}

Faqat quyidagi JSON array formatida javob ber, boshqa hech qanday matn qo'shma:
[
    {{"question": "Savol matni?", "options": ["Variant A", "Variant B", "Variant C", "Variant D"], "correct": 0}}
]
(correct - to'g'ri javobning indeksi: 0, 1, 2 yoki 3)"""

    try:
        response = await deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "Sen faqat JSON formatda javob beradigan quiz generatori."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=8000
        )
        
        content = response.choices[0].message.content.strip()
        
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        
        json_match = re.search(r'\[[\s\S]*\]', content)
        if json_match:
            content = json_match.group()
        
        questions = json.loads(content)
        
        valid_questions = []
        for q in questions:
            if isinstance(q, dict) and "question" in q and "options" in q and "correct" in q:
                if isinstance(q["options"], list) and len(q["options"]) >= 2:
                    q["options"] = q["options"][:10]
                    q["correct"] = min(max(0, int(q["correct"])), len(q["options"]) - 1)
                    valid_questions.append(q)
        
        return valid_questions[:count] if valid_questions else []
        
    except Exception as e:
        logging.error(f"DeepSeek AI parsing error: {e}")
        return []

async def generate_questions_with_deepseek(subject: str, age: str, count: int = 30) -> list:
    age_descriptions = {
        "10-18": "o'rta maktab o'quvchilari (10-18 yosh), oson va o'rtacha qiyinlikdagi",
        "18-25": "universitet talabalari (18-25 yosh), o'rtacha va qiyin darajadagi",
        "25-35": "kattalar (25-35 yosh), qiyin va murakkab"
    }
    age_desc = age_descriptions.get(age, "o'rtacha qiyinlikdagi")
    
    prompt = f"""Sen quiz savollari yaratuvchi sun'iy intellektsiz. 

{subject} mavzusidan {age_desc} savollar yarat.

Talablar:
1. Jami {count} ta savol
2. Har bir savol 4 ta javob varianti
3. Faqat JSON formatda javob ber

JSON: [{{"question": "Savol?", "options": ["A", "B", "C", "D"], "correct": 0}}]
Faqat JSON array qaytar!"""

    try:
        response = await deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "Sen faqat JSON formatda javob beradigan quiz generatori."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=8000
        )
        
        content = response.choices[0].message.content.strip()
        
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        
        json_match = re.search(r'\[[\s\S]*\]', content)
        if json_match:
            content = json_match.group()
        
        questions = json.loads(content)
        
        valid_questions = []
        for q in questions:
            if isinstance(q, dict) and "question" in q and "options" in q and "correct" in q:
                if isinstance(q["options"], list) and len(q["options"]) >= 2:
                    q["options"] = q["options"][:min(len(q["options"]), 10)]
                    q["correct"] = min(max(0, int(q["correct"])), len(q["options"]) - 1)
                    valid_questions.append(q)
        
        return valid_questions[:count] if valid_questions else []
        
    except Exception as e:
        logging.error(f"DeepSeek API error: {e}")
        return []

# ============ QUIZ POLL FUNCTIONS ============

async def send_quiz_poll(message: Message, user_id: int):
    """Send current question as Telegram Quiz Poll"""
    if user_id not in user_data:
        return
    
    data = user_data[user_id]
    current = data.get("current_question", 0)
    questions = data.get("questions", [])
    
    if current >= len(questions):
        # Quiz finished - show Result and Next buttons
        end_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Natija", callback_data="show_final_results")],
            [InlineKeyboardButton(text="➡️ Keyingisi", callback_data="next_quiz")]
        ])
        
        await message.answer(
            f"🏁 <b>Barcha savollar tugadi!</b>\n\n"
            f"Tanlang:",
            reply_markup=end_keyboard
        )
        return
    
    question = questions[current]
    
    poll_message = await message.answer_poll(
        question=f"❓ Savol {current + 1}/{len(questions)}\n\n{question['question'][:255]}",
        options=question["options"][:10],
        type=PollType.QUIZ,
        correct_option_id=question["correct"],
        is_anonymous=False,
        open_period=data.get("time", 30)
    )
    
    data["current_poll_id"] = poll_message.poll.id
    
    # Add results button under poll
    results_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Natijalarim", callback_data="show_results")]
    ])
    await message.answer("👆 Javob bering yoki:", reply_markup=results_keyboard)

@dp.poll_answer()
async def poll_answer_handler(poll_answer: PollAnswer):
    """Handle quiz poll answers and track scoring"""
    user_id = poll_answer.user.id
    
    if user_id not in user_data:
        return
    
    data = user_data[user_id]
    
    # Check if this is the current poll
    if poll_answer.poll_id != data.get("current_poll_id"):
        return
        
    # Get current question info
    current_idx = data.get("current_question", 0)
    questions = data.get("questions", [])
    
    if current_idx < len(questions):
        question = questions[current_idx]
        correct_idx = question["correct"]
        
        # Check if user's answer is correct
        if poll_answer.option_ids and poll_answer.option_ids[0] == correct_idx:
            data["correct_answers"] = data.get("correct_answers", 0) + 1
            logging.info(f"User {user_id} answered correctly!")
            
    # Move to next question index
    data["current_question"] = current_idx + 1
    
    # Small delay for visual feedback in Telegram
    await asyncio.sleep(2)
    
    chat_id = data.get("chat_id")
    if chat_id:
        await send_next_quiz_question(chat_id, user_id)

async def send_next_quiz_question(chat_id: int, user_id: int):
    if user_id not in user_data:
        return
    
    data = user_data[user_id]
    current = data.get("current_question", 0)
    questions = data.get("questions", [])
    
    # Show buttons if: 
    # 1. All questions are finished
    # 2. 30 questions in this "round" are finished
    round_size = 30
    if current >= len(questions) or (current > 0 and current % round_size == 0):
        end_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Natija", callback_data="show_final_results")],
            [InlineKeyboardButton(text="➡️ Keyingisi", callback_data="next_quiz")]
        ])
        
        msg = "🏁 <b>Barcha savollar tugadi!</b>" if current >= len(questions) else f"✅ <b>{current} ta savol tugadi!</b>"
        
        await bot.send_message(
            chat_id,
            f"{msg}\n\n"
            f"Davom etishni xohlaysizmi?",
            reply_markup=end_keyboard
        )
        return
    
    question = questions[current]
    
    poll_message = await bot.send_poll(
        chat_id=chat_id,
        question=f"❓ Savol {current + 1}/{len(questions)}\n\n{question['question'][:255]}",
        options=question["options"][:10],
        type=PollType.QUIZ,
        correct_option_id=question["correct"],
        is_anonymous=False,
        open_period=data.get("time", 30)
    )
    
    # CRITICAL: Save new poll ID for tracking
    data["current_poll_id"] = poll_message.poll.id
    
    # Add results button under poll
    results_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Natijalarim", callback_data="show_results")]
    ])
    await bot.send_message(chat_id, "👆 Javob bering yoki:", reply_markup=results_keyboard)

# ============ RESULTS CALLBACK ============

@dp.callback_query(F.data == "show_results")
async def show_results_callback(callback: CallbackQuery, state: FSMContext):
    """Handle Natijalarim button press - end quiz and show results"""
    user_id = callback.from_user.id
    
    if user_id not in user_data:
        await callback.answer("❌ Quiz topilmadi. /start bosing.")
        return
    
    data = user_data[user_id]
    correct = data.get("correct_answers", 0)
    current = data.get("current_question", 0)
    questions = data.get("questions", [])
    total = len(questions)
    
    if current == 0:
        await callback.answer("Hali savollarga javob bermadingiz!")
        return
    
    percentage = (correct / current) * 100 if current > 0 else 0
    
    if percentage >= 85:
        grade = "🏆 A'lo! (Siz mastersiz!)"
    elif percentage >= 70:
        grade = "👍 Yaxshi! (Yana ozgina harakat)"
    elif percentage >= 50:
        grade = "📚 O'rtacha (Ko'proq o'qing)"
    else:
        grade = "💪 Ko'proq mashq qiling (Davom eting!)"
    
    await callback.message.edit_text(
        f"🏁 <b>Quiz to'xtatildi!</b>\n\n"
        f"📊 <b>Natijangiz:</b>\n"
        f"✅ To'g'ri: {correct} ta\n"
        f"❌ Noto'g'ri: {current - correct} ta\n"
        f"📈 Foiz: {percentage:.1f}%\n"
        f"🎯 Baho: {grade}\n\n"
        f"<i>Jami {total} tadan {current} tasiga javob berdingiz.</i>"
    )
    
    await callback.answer("Quiz tugatildi!")
    
    await bot.send_message(
        callback.message.chat.id,
        "Bosh menyu:",
        reply_markup=main_menu
    )
    
    await state.clear()

@dp.callback_query(F.data == "show_final_results")
async def show_final_results_callback(callback: CallbackQuery):
    """Show results at the end of the quiz"""
    user_id = callback.from_user.id
    if user_id not in user_data:
        await callback.answer("❌ Xatolik")
        return
        
    data = user_data[user_id]
    correct = data.get("correct_answers", 0)
    total = len(data.get("questions", []))
    percentage = (correct / total) * 100 if total > 0 else 0
    
    if percentage >= 85:
        grade = "🏆 A'lo! (Siz mastersiz!)"
    elif percentage >= 70:
        grade = "👍 Yaxshi! (Yana ozgina harakat)"
    elif percentage >= 50:
        grade = "📚 O'rtacha (Ko'proq o'qing)"
    else:
        grade = "💪 Ko'proq mashq qiling (Davom eting!)"
        
    await callback.message.edit_text(
        f"📊 <b>Quiz Yakuniy Natijasi:</b>\n\n"
        f"✅ To'g'ri: {correct} ta\n"
        f"❌ Noto'g'ri: {total - correct} ta\n"
        f"📈 Foiz: {percentage:.1f}%\n"
        f"🎯 Baho: {grade}\n\n"
        f"<i>Jami {total} ta savol bo'ldi.</i>"
    )
    await callback.answer()

@dp.callback_query(F.data == "next_quiz")
async def next_quiz_callback(callback: CallbackQuery, state: FSMContext):
    """Restore state to select time before continuing or generating new questions"""
    user_id = callback.from_user.id
    if user_id not in user_data:
        await callback.answer("❌ Xatolik")
        return
        
    data = user_data[user_id]
    current = data.get("current_question", 0)
    questions = data.get("questions", [])
    
    # If we have more questions in the list (e.g. from a large file)
    if current < len(questions):
        await state.set_state(QuizStates.file_selecting_time)
        await callback.message.edit_text("⏱️ Keyingi bosqich uchun vaqtni tanlang:", reply_markup=time_menu)
    else:
        # Need to generate new questions via AI
        subject = data.get("subject")
        topic = data.get("custom_topic")
        
        if topic:
            await state.set_state(QuizStates.topic_selecting_time)
            await callback.message.edit_text(f"📚 Mavzu: <b>{topic}</b>\n\n⏱️ Vaqtni tanlang:", reply_markup=time_menu)
        elif subject:
            await state.set_state(QuizStates.selecting_time)
            await callback.message.edit_text(f"📚 Fan: <b>{data.get('subject_name')}</b>\n\n⏱️ Vaqtni tanlang:", reply_markup=time_menu)
        else:
            await callback.message.edit_text("📚 Yangi quiz uchun vaqtni tanlang:", reply_markup=time_menu)
            await state.set_state(QuizStates.selecting_time)
            
    await callback.answer()

# ============ HANDLERS ============

@dp.message(CommandStart())
async def command_start_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_data[message.from_user.id] = {"chat_id": message.chat.id}
    await message.answer(
        f"🎉 Salom, {html.bold(message.from_user.full_name)}!\n\n"
        f"🤖 <b>AI Quiz Bot</b>\n\n"
        f"👇 Yoshingizni tanlang:",
        reply_markup=age_menu
    )

@dp.message(F.text.in_({"👶 10-18", "👨 18-25", "👴 25-35"}))
async def age_selection_handler(message: Message, state: FSMContext) -> None:
    age = message.text.split(" ")[1]
    if message.from_user.id not in user_data:
        user_data[message.from_user.id] = {}
    user_data[message.from_user.id]["age"] = age
    user_data[message.from_user.id]["chat_id"] = message.chat.id
    await message.answer(f"✅ {message.text}\n\n📋 Menyudan tanlang:", reply_markup=main_menu)

@dp.message(F.text == "🚀 Quiz boshlash")
async def quiz_start_handler(message: Message, state: FSMContext) -> None:
    await state.set_state(QuizStates.selecting_subject)
    await message.answer("📚 Fanni tanlang:", reply_markup=subject_menu)

@dp.message(F.text == "📝 Quiz yaratish")
async def quiz_creation_handler(message: Message, state: FSMContext) -> None:
    await message.answer(
        "📝 <b>Quiz yaratish</b>\n\n"
        "📚 <b>Mavzu tanlash</b> - AI savol yaratadi\n"
        "📁 <b>Fayl tanlash</b> - Fayldagi savollar",
        reply_markup=quiz_creation_menu
    )

@dp.message(F.text == "📚 Mavzu tanlash")
async def topic_selection_handler(message: Message, state: FSMContext) -> None:
    await state.set_state(QuizStates.entering_topic)
    await message.answer("📚 <b>Mavzu kiriting:</b>", reply_markup=cancel_menu)

@dp.message(QuizStates.entering_topic, F.text != "❌ Bekor qilish")
async def topic_entered_handler(message: Message, state: FSMContext) -> None:
    user_data[message.from_user.id]["custom_topic"] = message.text
    await state.set_state(QuizStates.topic_selecting_time)
    await message.answer(f"✅ Mavzu: <b>{message.text}</b>\n\n⏱️ Vaqt:", reply_markup=time_menu)

@dp.message(QuizStates.topic_selecting_time, F.text.in_({"⏱️ 30 soniya", "⏱️ 1 daqiqa", "⏱️ 3 daqiqa"}))
async def topic_time_selected_handler(message: Message, state: FSMContext) -> None:
    time_map = {"⏱️ 30 soniya": 30, "⏱️ 1 daqiqa": 60, "⏱️ 3 daqiqa": 180}
    user_id = message.from_user.id
    user_data[user_id]["time"] = time_map.get(message.text, 30)
    user_data[user_id]["chat_id"] = message.chat.id
    
    age = user_data[user_id].get("age", "18-25")
    topic = user_data[user_id].get("custom_topic", "Umumiy bilim")
    
    loading_msg = await message.answer(f"🤖 DeepSeek AI savollar tayyorlamoqda...\n📚 {topic}", reply_markup=cancel_menu)
    
    questions = await generate_questions_with_deepseek(topic, age, 30)
    
    if not questions:
        await loading_msg.edit_text("❌ Xatolik. Qayta urinib ko'ring.")
        await message.answer("Menyu:", reply_markup=main_menu)
        await state.clear()
        return
    
    user_data[user_id]["questions"] = questions
    user_data[user_id]["current_question"] = 0
    user_data[user_id]["correct_answers"] = 0
    
    await state.set_state(QuizStates.answering)
    await loading_msg.delete()
    await message.answer(f"🚀 Quiz boshlanmoqda!\n📝 {len(questions)} ta savol", reply_markup=quiz_menu)
    await send_quiz_poll(message, user_id)

# ============ FILE UPLOAD - PARSE EXISTING QUESTIONS ============

@dp.message(F.text.contains("📁 Fayl tanlash"))
async def file_selection_handler(message: Message, state: FSMContext) -> None:
    logging.info(f"User {message.from_user.id} clicked Fayl tanlash")
    await state.set_state(QuizStates.waiting_file)
    await message.answer(
        "📁 <b>Fayl yuklang</b>\n\n"
        "Fayldagi savol va javoblar o'qiladi. Agar tayyor savollar bo'lmasa, AI ularni matndan o'zi yaratadi.\n\n"
        "📄 PDF | 📝 DOCX | 📃 TXT",
        reply_markup=cancel_menu
    )

@dp.message(QuizStates.waiting_file, F.document)
async def file_received_handler(message: Message, state: FSMContext) -> None:
    document = message.document
    file_name = document.file_name.lower()
    
    if not any(file_name.endswith(ext) for ext in ['.pdf', '.docx', '.txt']):
        await message.answer("❌ Faqat PDF, DOCX yoki TXT!", reply_markup=cancel_menu)
        return
    
    loading_msg = await message.answer("📥 Fayl o'qilmoqda...")
    
    try:
        file = await bot.get_file(document.file_id)
        ext = os.path.splitext(file_name)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            await bot.download_file(file.file_path, tmp.name)
            tmp_path = tmp.name
        
        if file_name.endswith('.pdf'):
            text = await extract_text_from_pdf(tmp_path)
        elif file_name.endswith('.docx'):
            text = await extract_text_from_docx(tmp_path)
        else:
            text = await extract_text_from_txt(tmp_path)
        
        try:
            os.unlink(tmp_path)
        except:
            pass
        
        if not text or len(text) < 50:
            await loading_msg.edit_text("❌ Fayldan matn o'qib bo'lmadi.")
            return
        
        # Parse questions from file
        questions = parse_questions_from_text(text)
        
        if not questions:
            # Fallback to AI parsing if local parsing fails
            logging.info("Local parsing failed, falling back to DeepSeek AI parsing")
            await loading_msg.edit_text("🤖 AI savollarni aniqlamoqda... (Kuting)")
            
            # Ensure user_data exists for this user
            uid = message.from_user.id
            if uid not in user_data:
                user_data[uid] = {"chat_id": message.chat.id}
                
            age = user_data[uid].get("age", "18-25")
            questions = await generate_questions_from_text(text, age, 30)
        
        if not questions:
            await loading_msg.edit_text(
                "❌ Savollar topilmadi.\n\n"
                "Faylda savollar va javoblar borligiga ishonch hosil qiling."
            )
            return
        
        # Shuffle answer options
        questions = shuffle_options(questions)
        
        user_id = message.from_user.id
        user_data[user_id]["questions"] = questions
        user_data[user_id]["file_name"] = document.file_name
        user_data[user_id]["chat_id"] = message.chat.id
        
        await loading_msg.delete()
        await state.set_state(QuizStates.file_selecting_time)
        
        await message.answer(
            f"✅ <b>{len(questions)} ta savol tayyor!</b>\n"
            f"📁 Fayl: {document.file_name}\n\n"
            f"⏱️ Har bir savol uchun vaqt:",
            reply_markup=time_menu
        )
        
    except Exception as e:
        import traceback
        logging.error(f"File error: {e}\n{traceback.format_exc()}")
        await loading_msg.edit_text("❌ Xatolik yuz berdi. Qayta urinib ko'ring.")

@dp.message(QuizStates.file_selecting_time, F.text.in_({"⏱️ 30 soniya", "⏱️ 1 daqiqa", "⏱️ 3 daqiqa"}))
async def file_time_selected_handler(message: Message, state: FSMContext) -> None:
    time_map = {"⏱️ 30 soniya": 30, "⏱️ 1 daqiqa": 60, "⏱️ 3 daqiqa": 180}
    user_id = message.from_user.id
    data = user_data[user_id]
    data["time"] = time_map.get(message.text, 30)
    data["chat_id"] = message.chat.id
    
    questions = data.get("questions", [])
    file_name = data.get("file_name", "Fayl")
    
    # Only reset if we are starting from the very beginning or if somehow empty
    if "current_question" not in data or data.get("current_question") >= len(questions):
        data["current_question"] = 0
        data["correct_answers"] = 0
    
    current_idx = data.get("current_question", 0)
    
    await state.set_state(QuizStates.answering)
    
    await message.answer(
        f"🚀 <b>Quiz {'davom etmoqda' if current_idx > 0 else 'boshlanmoqda'}!</b>\n\n"
        f"📁 {file_name}\n"
        f"📝 {len(questions)} ta savol\n"
        f"🔀 Savol: {current_idx + 1}-{min(current_idx + 30, len(questions))}",
        reply_markup=quiz_menu
    )
    
    await send_quiz_poll(message, user_id)

# ============ STANDARD QUIZ HANDLERS ============

@dp.message(QuizStates.selecting_subject, F.text.in_({"📐 Matematika", "📖 Ona tili", "🏛️ Tarix", "🇬🇧 Ingliz tili", "⚡ Fizika"}))
async def subject_selection_handler(message: Message, state: FSMContext) -> None:
    subject_map = {
        "📐 Matematika": "Matematika",
        "📖 Ona tili": "O'zbek tili va adabiyoti",
        "🏛️ Tarix": "O'zbekiston va jahon tarixi",
        "🇬🇧 Ingliz tili": "Ingliz tili",
        "⚡ Fizika": "Fizika"
    }
    user_data[message.from_user.id]["subject"] = subject_map.get(message.text, "Matematika")
    user_data[message.from_user.id]["subject_name"] = message.text
    
    await state.set_state(QuizStates.selecting_time)
    await message.answer(f"✅ {message.text}\n\n⏱️ Vaqt:", reply_markup=time_menu)

@dp.message(QuizStates.selecting_time, F.text.in_({"⏱️ 30 soniya", "⏱️ 1 daqiqa", "⏱️ 3 daqiqa"}))
async def time_selection_handler(message: Message, state: FSMContext) -> None:
    time_map = {"⏱️ 30 soniya": 30, "⏱️ 1 daqiqa": 60, "⏱️ 3 daqiqa": 180}
    user_id = message.from_user.id
    user_data[user_id]["time"] = time_map.get(message.text, 30)
    user_data[user_id]["chat_id"] = message.chat.id
    
    age = user_data[user_id].get("age", "18-25")
    subject = user_data[user_id].get("subject", "Matematika")
    subject_name = user_data[user_id].get("subject_name", "📐 Matematika")
    
    loading_msg = await message.answer(f"🤖 DeepSeek AI savollar tayyorlamoqda...\n📚 {subject_name}", reply_markup=cancel_menu)
    
    questions = await generate_questions_with_deepseek(subject, age, 30)
    
    if not questions:
        await loading_msg.edit_text("❌ Xatolik. Qayta urinib ko'ring.")
        await message.answer("Menyu:", reply_markup=main_menu)
        await state.clear()
        return
    
    user_data[user_id]["questions"] = questions
    user_data[user_id]["current_question"] = 0
    user_data[user_id]["correct_answers"] = 0
    
    await state.set_state(QuizStates.answering)
    await loading_msg.delete()
    await message.answer(f"🚀 Quiz boshlanmoqda!\n📝 {len(questions)} ta savol", reply_markup=quiz_menu)
    await send_quiz_poll(message, user_id)

@dp.message(F.text.in_({"🛑 Quizni tugatish", "❌ Bekor qilish"}))
async def stop_quiz_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    if user_id in user_data:
        data = user_data[user_id]
        correct = data.get("correct_answers", 0)
        current = data.get("current_question", 0)
        if current > 0:
            await message.answer(f"🛑 Quiz tugatildi!\n✅ {correct}/{current}", reply_markup=main_menu)
        else:
            await message.answer("❌ Bekor qilindi.", reply_markup=main_menu)
    else:
        await message.answer("Menyu:", reply_markup=main_menu)
    await state.clear()

@dp.message(F.text == "🗣️ Murojatlar")
async def murojatlar_handler(message: Message) -> None:
    await message.answer(
        "🗣️ <b>Murojatlar va takliflar uchun guruhimizga qo'shiling:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👥 Guruhga qo'shilish", url="https://t.me/quizbotgroup1")]
        ])
    )

@dp.message(F.text == "⬅️ Ortga")
async def back_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("📋 Menyu:", reply_markup=main_menu)

@dp.message(F.text == "ℹ️ Yordam")
async def help_handler(message: Message) -> None:
    help_text = (
        "🤖 <b>Bot Vazifasi:</b>\n"
        "Ushbu bot sizga turli fanlar va mavzularda bilimlaringizni sinash uchun quizlar (testlar) yaratishga yordam beradi. "
        "DeepSeek AI orqali savollar dinamik ravishda yaratiladi.\n\n"
        "🕹️ <b>Tugmalar vazifasi:</b>\n"
        "🚀 <b>Quiz boshlash</b> - Tayyor fanlardan birini tanlaysiz va AI sizga 30 ta savol yaratib beradi.\n"
        "📝 <b>Quiz yaratish:</b>\n"
        "  └ 📚 <b>Mavzu tanlash</b> - O'zingiz xohlagan ixtiyoriy mavzuni yozing, AI shunga mos test tuzadi.\n"
        "  └ 📁 <b>Fayl tanlash</b> - PDF, DOCX yoki TXT fayl yuklang. Bot matnni o'qib savollarni aniqlaydi.\n"
        "🗣️ <b>Murojatlar</b> - Bot guruhi va takliflar uchun havola.\n\n"
        "🔄 <b>30 talik tizim:</b>\n"
        "Quiz har 30 ta savoldan keyin to'xtaydi. Natijani ko'rishingiz yoki 'Keyingisi' tugmasi orqali yana 30 ta yangi savol bilan davom etishingiz mumkin.\n\n"
        "🆘 <b>Savol va takliflar:</b>"
    )
    
    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨‍💻 Admin (Suhrob)", url="https://t.me/Suhrob031")]
    ])
    
    await message.answer(help_text, reply_markup=admin_keyboard)

@dp.message()
async def echo_handler(message: Message) -> None:
    await message.answer("🤔 Menyudan tanlang.", reply_markup=main_menu)

async def main() -> None:
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
