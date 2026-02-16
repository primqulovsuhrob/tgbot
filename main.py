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
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, PollAnswer, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from openai import AsyncOpenAI
from config import BOT_TOKEN, DEEPSEEK_API_KEY
import matplotlib
import matplotlib.pyplot as plt
import io
import platform
import time
from datetime import datetime

matplotlib.use('Agg') # Use non-interactive backend

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
DATA_FILE = "data.json"
QUIZ_FILE = "quizzes.json"
ADMIN_USERNAME = "Suhrob031"
ADMIN_ID = 8170458930 # Based on data.json
BOT_START_TIME = time.time()
PHOTOS_DIR = "photos"
os.makedirs(PHOTOS_DIR, exist_ok=True)

def load_json(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_json(file_path, data):
    with open(file_path, "w") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_data():
    data = load_json(DATA_FILE)
    if "users" not in data: data["users"] = {}
    if "results" not in data: data["results"] = {}
    if "stats" not in data: data["stats"] = {}
    
    # Migration: Old stats keys to new ones
    stats = data["stats"]
    if "deepseek_durations" in stats and "ai_durations" not in stats:
        stats["ai_durations"] = stats["deepseek_durations"]
    return data

def save_user_result(user_id, full_name, username, score, total_questions, time_spent=0, subject=None):
    data = get_data()
    uid = str(user_id)
    
    # Update results
    results = data["results"]
    if uid not in results:
        results[uid] = {
            "name": full_name,
            "username": username,
            "best_score": score,
            "total_correct": score,
            "total_questions": total_questions,
            "quizzes_count": 1,
            "total_time": time_spent,
            "subjects": [subject] if subject else [],
            "date": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
    else:
        # Update best score
        if score > results[uid].get("best_score", 0):
            results[uid]["best_score"] = score
            results[uid]["date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        # Incremental updates
        results[uid]["total_correct"] = results[uid].get("total_correct", 0) + score
        results[uid]["total_questions"] = results[uid].get("total_questions", 0) + total_questions
        results[uid]["quizzes_count"] = results[uid].get("quizzes_count", 0) + 1
        results[uid]["total_time"] = results[uid].get("total_time", 0) + time_spent
        
        if subject:
            subjects = results[uid].get("subjects", [])
            if subject not in subjects:
                subjects.append(subject)
            results[uid]["subjects"] = subjects

    save_json(DATA_FILE, data)

def track_error(error_msg):
    """Log bot errors to data.json"""
    data = get_data()
    stats = data["stats"]
    if "errors" not in stats:
        stats["errors"] = []
    
    stats["errors"].append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "msg": str(error_msg)[:200]
    })
    # Keep last 50 errors
    stats["errors"] = stats["errors"][-50:]
    save_json(DATA_FILE, data)

async def save_user(user):
    """Save a user who pressed /start and download their profile photo if it doesn't exist"""
    data = get_data()
    uid = str(user.id)
    users = data["users"]
    
    if uid not in users:
        users[uid] = {
            "id": user.id,
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "full_name": user.full_name or "",
            "username": user.username or "",
            "joined_date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "photo": None
        }
    else:
        users[uid]["first_name"] = user.first_name or ""
        users[uid]["last_name"] = user.last_name or ""
        users[uid]["full_name"] = user.full_name or ""
        users[uid]["username"] = user.username or ""

    # Download profile photo if not already saved
    if not users[uid].get("photo"):
        try:
            photos = await bot.get_user_profile_photos(user.id, limit=1)
            if photos.total_count > 0:
                photo = photos.photos[0][-1] # Get high res
                file_info = await bot.get_file(photo.file_id)
                photo_ext = file_info.file_path.split('.')[-1]
                photo_name = f"{uid}.{photo_ext}"
                photo_path = os.path.join(PHOTOS_DIR, photo_name)
                
                await bot.download_file(file_info.file_path, photo_path)
                users[uid]["photo"] = photo_path
                logging.info(f"Saved profile photo for user {uid}")
        except Exception as e:
            logging.error(f"Error saving profile photo for user {uid}: {e}")
        
    save_json(DATA_FILE, data)

def update_user_age(user_id, age):
    data = get_data()
    uid = str(user_id)
    if uid in data["users"]:
        data["users"][uid]["age"] = age
        save_json(DATA_FILE, data)

def get_stored_user(user_id):
    data = get_data()
    return data["users"].get(str(user_id))

def track_deepseek_usage(duration=0):
    """Track DeepSeek API call count, times, and duration"""
    data = get_data()
    stats = data["stats"]
    
    stats["deepseek_calls"] = stats.get("deepseek_calls", 0) + 1
    
    # Track speed
    if "ai_durations" not in stats:
        stats["ai_durations"] = []
    stats["ai_durations"].append(duration)
    stats["ai_durations"] = stats["ai_durations"][-100:] # Keep last 100
    
    today = datetime.now().strftime("%Y-%m-%d")
    if "daily_calls" not in stats:
        stats["daily_calls"] = {}
    stats["daily_calls"][today] = stats["daily_calls"].get(today, 0) + 1
    stats["last_call"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    save_json(DATA_FILE, data)

def get_server_info():
    """Get server and bot information"""
    uptime_seconds = int(time.time() - BOT_START_TIME)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"
    
    return {
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "uptime": uptime_str,
        "machine": platform.machine()
    }

def get_main_menu(username: str = None, user_id: int = None) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="📝 Quiz yaratish"), KeyboardButton(text="🚀 Quiz boshlash")],
        [KeyboardButton(text="📊 Natijalarim"), KeyboardButton(text="🗣️ Murojatlar")],
        [KeyboardButton(text="ℹ️ Yordam")]
    ]
    
    is_admin = False
    if user_id == ADMIN_ID:
        is_admin = True
    elif username and username.lower() == ADMIN_USERNAME.lower():
        is_admin = True
        
    if is_admin:
        buttons.append([KeyboardButton(text="🔑 Admen")])
    
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Natijalar"), KeyboardButton(text="📈 Reyting")],
        [KeyboardButton(text="👥 Obunchilar")],
        [KeyboardButton(text="⬅️ Ortga")]
    ],
    resize_keyboard=True
)

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

# Keyboards (rest)

age_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👶 10-18"), KeyboardButton(text="👨 18-25")],
        [KeyboardButton(text="👴 25-35")]
    ],
    resize_keyboard=True
)

def get_subject_keyboard() -> ReplyKeyboardMarkup:
    """Dynamically generate subject menu including cached ones"""
    buttons = [
        [KeyboardButton(text="📐 Matematika"), KeyboardButton(text="📖 Ona tili")],
        [KeyboardButton(text="🏛️ Tarix"), KeyboardButton(text="🇬🇧 Ingliz tili")],
        [KeyboardButton(text="⚡ Fizika")]
    ]
    
    # Load from quizzes.json
    quizzes = load_json(QUIZ_FILE)
    existing_texts = {"📐 Matematika", "📖 Ona tili", "🏛️ Tarix", "🇬🇧 Ingliz tili", "⚡ Fizika", "⬅️ Ortga"}
    
    extra_buttons = []
    # Add files from Yuklangan Fayllar
    if "Yuklangan Fayllar" in quizzes:
        for file_name in quizzes["Yuklangan Fayllar"]:
            btn_text = f"📁 {file_name}"
            if btn_text not in existing_texts:
                extra_buttons.append(KeyboardButton(text=btn_text))
                existing_texts.add(btn_text)
    
    # Add other subjects
    for subject in quizzes:
        if subject != "Yuklangan Fayllar" and subject not in ["Matematika", "O'zbek tili va adabiyoti", "O'zbekiston va jahon tarixi", "Ingliz tili", "Fizika"]:
            if subject not in existing_texts:
                extra_buttons.append(KeyboardButton(text=subject))
                existing_texts.add(subject)
                
    # Chunk extra buttons into rows of 2
    for i in range(0, len(extra_buttons), 2):
        buttons.append(extra_buttons[i:i+2])
        
    buttons.append([KeyboardButton(text="⬅️ Ortga")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

subject_menu = get_subject_keyboard() # Keep as default but handlers should call function

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

    start_time = time.time()
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
        
        duration = time.time() - start_time
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
        
        track_deepseek_usage(duration)
        if valid_questions:
            valid_questions = shuffle_options(valid_questions)
        return valid_questions[:count] if valid_questions else []
        
    except Exception as e:
        track_error(f"DeepSeek AI parsing error: {e}")
        logging.error(f"DeepSeek AI parsing error: {e}")
        return []

async def generate_questions_with_deepseek(subject: str, age: str, count: int = 30) -> list:
    # 1. Check Cache (quizzes.json)
    quizzes = load_json(QUIZ_FILE)
    cached_questions = []
    if subject in quizzes and age in quizzes[subject]:
        cached_questions = list(quizzes[subject][age])
        if len(cached_questions) >= count:
            logging.info(f"Using cached questions for {subject} {age}")
            # Return random selection from cache
            return random.sample(cached_questions, count)
    
    num_to_generate = count - len(cached_questions)
    logging.info(f"Supplementing {subject} {age} with {num_to_generate} new AI questions.")

    age_descriptions = {
        "10-18": "o'rta maktab o'quvchilari (10-18 yosh), oson va o'rtacha qiyinlikdagi",
        "18-25": "universitet talabalari (18-25 yosh), o'rtacha va qiyin darajadagi",
        "25-35": "kattalar (25-35 yosh), qiyin va murakkab"
    }
    age_desc = age_descriptions.get(age, "o'rtacha qiyinlikdagi")
    
    prompt = f"""Sen quiz savollari yaratuvchi sun'iy intellektsiz. 

{subject} mavzusidan {age_desc} {num_to_generate} ta yangi savol yarat.

Talablar:
1. Jami {num_to_generate} ta savol
2. Har bir savol 4 ta javob varianti
3. Faqat JSON formatda javob ber

JSON: [{{"question": "Savol?", "options": ["A", "B", "C", "D"], "correct": 0}}]
Faqat JSON array qaytar!"""

    start_time = time.time()
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
        
        duration = time.time() - start_time
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
        
        # Save to Cache
        if valid_questions:
            quizzes = load_json(QUIZ_FILE)
            if subject not in quizzes:
                quizzes[subject] = {}
            if age not in quizzes[subject]:
                quizzes[subject][age] = []
            
            # Extend existing questions, avoiding exact duplicates
            existing_questions = {q["question"] for q in quizzes[subject][age]}
            for q in valid_questions:
                if q["question"] not in existing_questions:
                    quizzes[subject][age].append(q)
            
            save_json(QUIZ_FILE, quizzes)
        
        track_deepseek_usage(duration)
        
        # Combine cached and new questions
        combined = cached_questions + valid_questions
        if combined:
            combined = shuffle_options(combined)
        return combined[:count] if combined else []
        
    except Exception as e:
        track_error(f"DeepSeek API error: {e}")
        logging.error(f"DeepSeek API error: {e}")
        return []



async def handle_poll_timeout(chat_id: int, user_id: int, poll_id: str, timeout: int):
    """Wait for timeout, then check if answered. If not, mark incorrect and move on."""
    await asyncio.sleep(timeout + 2)  # Wait slightly longer than poll open_period
    
    if user_id not in user_data:
        return
        
    data = user_data[user_id]
    
    # Check if this poll is still the current active one
    if data.get("current_poll_id") != poll_id:
        return
        
    # Check if already answered
    if data.get("poll_answered", False):
        return
        
    # If not answered, mark as incorrect (don't increment correct_answers)
    data["consecutive_timeouts"] = data.get("consecutive_timeouts", 0) + 1
    
    if data["consecutive_timeouts"] >= 3:
        try:
            # Save results before stopping
            correct = data.get("correct_answers", 0)
            
            # Try to get user info from storage if not in current session data
            user_info = get_stored_user(user_id)
            full_name = "Foydalanuvchi"
            username = "None"
            
            if user_info:
                full_name = user_info.get("full_name", "Foydalanuvchi")
                username = user_info.get("username", "")

            # Save the result
            time_spent = int(time.time() - data.get("start_quiz_time", time.time()))
            subject = data.get("subject", "Noma'lum")
            save_user_result(user_id, full_name, username, correct, data.get("current_question", 0), time_spent, subject)
            
            await bot.send_message(
                chat_id, 
                f"🛑 <b>Siz ketma-ket 3 marta javob bermadingiz!</b>\n\n"
                f"🏁 <b>O'yin yakunlandi.</b>\n"
                f"✅ To'g'ri javoblar: <b>{correct}</b> ta",
                reply_markup=get_main_menu(username, user_id)
            )
            # Clear state and session data
            state_ctx = FSMContext(
                storage=dp.storage,
                key=StorageKey(bot_id=bot.id, chat_id=chat_id, user_id=user_id)
            )
            await state_ctx.clear()
            
            if user_id in user_data:
                del user_data[user_id]
        except Exception as e:
            logging.error(f"Timeout stop error: {e}")
        return

    current_idx = data.get("current_question", 0)
    questions = data.get("questions", [])
    
    # Send timeout message
    if current_idx < len(questions):
        try:
            await bot.send_message(chat_id, f"⏰ <b>Vaqt tugadi!</b> ({data['consecutive_timeouts']}/3)")
        except:
            pass
            
    # Move to next question
    data["current_question"] = current_idx + 1
    
    # Send next question
    await send_next_quiz_question(chat_id, user_id)

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
    
    data["poll_answered"] = False
    
    # Start timeout timer
    asyncio.create_task(handle_poll_timeout(
        chat_id=message.chat.id,
        user_id=user_id,
        poll_id=poll_message.poll.id,
        timeout=data.get("time", 30)
    ))
    
    # Add difficulty and results buttons under poll
    results_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 Sodda", callback_data="regen_easy"),
            InlineKeyboardButton(text="🔴 Murakkab", callback_data="regen_hard")
        ],
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
        
    # Mark as answered to prevent timeout logic
    data["poll_answered"] = True
    data["consecutive_timeouts"] = 0 # Reset counter on answer
        
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
    
    # CRITICAL: Save new poll ID for tracking and reset answered flag
    data["current_poll_id"] = poll_message.poll.id
    data["poll_answered"] = False
    
    # Start timeout timer
    asyncio.create_task(handle_poll_timeout(
        chat_id=chat_id,
        user_id=user_id,
        poll_id=poll_message.poll.id,
        timeout=data.get("time", 30)
    ))
    
    # Add difficulty and results buttons under poll
    results_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 Sodda", callback_data="regen_easy"),
            InlineKeyboardButton(text="🔴 Murakkab", callback_data="regen_hard")
        ],
        [InlineKeyboardButton(text="📊 Natijalarim", callback_data="show_results")]
    ])
    await bot.send_message(chat_id, "👆 Javob bering yoki:", reply_markup=results_keyboard)

# ============ RESULTS CALLBACK ============

@dp.callback_query(F.data.in_({"regen_easy", "regen_hard"}))
async def difficulty_regeneration_handler(callback: CallbackQuery, state: FSMContext):
    """Restart quiz with different difficulty (easy or hard)"""
    user_id = callback.from_user.id
    if user_id not in user_data:
        await callback.answer("❌ Seans topilmadi.")
        return
        
    data = user_data[user_id]
    subject = data.get("subject", "Matematika")
    subject_name = data.get("subject_name", "📐 Matematika")
    
    # Set new difficulty
    new_age = "10-18" if callback.data == "regen_easy" else "25-35"
    data["age"] = new_age
    update_user_age(user_id, new_age)
    
    # Reset and notify
    data["questions"] = [] 
    data["current_question"] = 0
    data["correct_answers"] = 0
    data["consecutive_timeouts"] = 0
    
    diff_text = "🟢 Sodda" if callback.data == "regen_easy" else "🔴 Murakkab"
    await callback.message.answer(f"🔄 <b>{subject_name} ({diff_text})</b> bo'yicha yangi savollar tayyorlanmoqda...")
    await callback.answer()
    
    # Generate new questions
    questions = await generate_questions_with_deepseek(subject, new_age, 30)
    if not questions:
        await callback.message.answer("❌ Savol yaratishda xatolik. Keyinroq urinib ko'ring.")
        return
        
    data["questions"] = shuffle_options(questions)
    data["start_quiz_time"] = time.time()
    
    await callback.message.answer(f"🚀 Yangi 30 ta savol tayyor!")
    await send_quiz_poll(callback.message, user_id)

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
        reply_markup=get_main_menu(callback.from_user.username, callback.from_user.id)
    )
    
    # Save result
    time_spent = int(time.time() - data.get("start_quiz_time", time.time()))
    subject = data.get("subject", "Noma'lum")
    save_user_result(
        user_id=callback.from_user.id,
        full_name=callback.from_user.full_name,
        username=callback.from_user.username,
        score=correct, total_questions=current,
        time_spent=time_spent,
        subject=subject
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

    # Save final result
    time_spent = int(time.time() - data.get("start_quiz_time", time.time()))
    subject = data.get("subject", "Noma'lum")
    save_user_result(
        user_id=callback.from_user.id,
        full_name=callback.from_user.full_name,
        username=callback.from_user.username,
        score=correct, total_questions=total,
        time_spent=time_spent,
        subject=subject
    )

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
    user_id = message.from_user.id
    user_data[user_id] = {"chat_id": message.chat.id}
    
    # Save user to persistent storage
    await save_user(message.from_user)
    
    # Check if user already has age
    stored_user = get_stored_user(user_id)
    if stored_user and "age" in stored_user:
        user_data[user_id]["age"] = stored_user["age"]
        await message.answer(
            f"🎉 Salom, {html.bold(message.from_user.full_name)}!\n\n"
            f"🤖 <b>AI Quiz Bot</b>\n\n"
            f"Siz avval ro'yxatdan o'tgansiz.",
            reply_markup=get_main_menu(message.from_user.username, message.from_user.id)
        )
    else:
        await message.answer(
            f"🎉 Salom, {html.bold(message.from_user.full_name)}!\n\n"
            f"🤖 <b>AI Quiz Bot</b>\n\n"
            f"👇 Yoshingizni tanlang:",
            reply_markup=age_menu
        )

@dp.message(F.text.in_({"👶 10-18", "👨 18-25", "👴 25-35"}))
async def age_selection_handler(message: Message, state: FSMContext) -> None:
    age = message.text.split(" ")[1]
    user_id = message.from_user.id
    if user_id not in user_data:
        user_data[user_id] = {}
    user_data[user_id]["age"] = age
    user_data[user_id]["chat_id"] = message.chat.id
    
    # Save age to file
    update_user_age(user_id, age)
    
    await message.answer(
        f"✅ {message.text}\n\n📋 Menyudan tanlang:", 
        reply_markup=get_main_menu(message.from_user.username, message.from_user.id)
    )

@dp.message(F.text == "🚀 Quiz boshlash")
async def quiz_start_handler(message: Message, state: FSMContext) -> None:
    await state.set_state(QuizStates.selecting_subject)
    await message.answer("📚 <b>Fanni tanlang:</b>\n<i>(O'zingiz yuklagan fayllar ham shu yerda)</i>", 
                         reply_markup=get_subject_keyboard())

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
    
    # Check Cache (quizzes.json) first to avoid "generating" message if it exists
    quizzes = load_json(QUIZ_FILE)
    if topic in quizzes and age in quizzes[topic] and len(quizzes[topic][age]) >= 30:
        questions = random.sample(quizzes[topic][age], 30)
        loading_msg = None
    else:
        loading_msg = await message.answer(f"🤖 DeepSeek AI savollar tayyorlamoqda...\n📚 {topic}", reply_markup=cancel_menu)
        questions = await generate_questions_with_deepseek(topic, age, 30)
    
    if not questions:
        if loading_msg:
            await loading_msg.edit_text("❌ Xatolik. Qayta urinib ko'ring.")
        else:
            await message.answer("❌ Xatolik. Qayta urinib ko'ring.")
        await message.answer("Menyu:", reply_markup=get_main_menu(message.from_user.username, message.from_user.id))
        await state.clear()
        return
    
    user_data[user_id]["questions"] = questions
    user_data[user_id]["current_question"] = 0
    user_data[user_id]["correct_answers"] = 0
    user_data[user_id]["start_quiz_time"] = time.time()
    user_data[user_id]["subject"] = topic
    
    await state.set_state(QuizStates.answering)
    if loading_msg:
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
        
        # Save to quizzes.json
        quizzes = load_json(QUIZ_FILE)
        file_key = "Yuklangan Fayllar"
        if file_key not in quizzes:
            quizzes[file_key] = {}
        
        # Use filename as sub-key (sanitize if needed, but simple is fine)
        # Store questions
        quizzes[file_key][document.file_name] = questions
        save_json(QUIZ_FILE, quizzes)
        
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
        data["start_quiz_time"] = time.time()
        data["subject"] = file_name
    
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

@dp.message(QuizStates.selecting_subject)
async def subject_selection_handler(message: Message, state: FSMContext) -> None:
    if message.text == "⬅️ Ortga":
        return await back_handler(message, state)
        
    subject_map = {
        "📐 Matematika": "Matematika",
        "📖 Ona tili": "O'zbek tili va adabiyoti",
        "🏛️ Tarix": "O'zbekiston va jahon tarixi",
        "🇬🇧 Ingliz tili": "Ingliz tili",
        "⚡ Fizika": "Fizika"
    }
    
    user_id = message.from_user.id
    if user_id not in user_data:
        user_data[user_id] = {"chat_id": message.chat.id}
    
    # Clear previous questions for a fresh start
    if "questions" in user_data[user_id]:
        del user_data[user_id]["questions"]
        
    text = message.text
    quizzes = load_json(QUIZ_FILE)
    
    if text in subject_map:
        user_data[user_id]["subject"] = subject_map[text]
        user_data[user_id]["subject_name"] = text
    elif text.startswith("📁 "):
        # File from Yuklangan Fayllar
        file_name = text[2:]
        if "Yuklangan Fayllar" in quizzes and file_name in quizzes["Yuklangan Fayllar"]:
            user_data[user_id]["questions"] = quizzes["Yuklangan Fayllar"][file_name]
            user_data[user_id]["subject"] = "Yuklangan Fayllar"
            user_data[user_id]["subject_name"] = text
        else:
            await message.answer("❌ Fayl topilmadi.")
            return
    elif text in quizzes:
        # Custom subject
        user_data[user_id]["subject"] = text
        user_data[user_id]["subject_name"] = text
    else:
        # AI will generate for this "new" subject
        user_data[user_id]["subject"] = text
        user_data[user_id]["subject_name"] = text
    
    await state.set_state(QuizStates.selecting_time)
    await message.answer(f"✅ {message.text}\n\n⏱️ Vaqtni tanlang:", reply_markup=time_menu)

@dp.message(QuizStates.selecting_time, F.text.in_({"⏱️ 30 soniya", "⏱️ 1 daqiqa", "⏱️ 3 daqiqa"}))
async def time_selection_handler(message: Message, state: FSMContext) -> None:
    time_map = {"⏱️ 30 soniya": 30, "⏱️ 1 daqiqa": 60, "⏱️ 3 daqiqa": 180}
    user_id = message.from_user.id
    user_data[user_id]["time"] = time_map.get(message.text, 30)
    user_data[user_id]["chat_id"] = message.chat.id
    
    age = user_data[user_id].get("age", "18-25")
    subject = user_data[user_id].get("subject", "Matematika")
    subject_name = user_data[user_id].get("subject_name", "📐 Matematika")
    
    # Check if questions already exist (e.g. from browsing or file)
    if "questions" in user_data[user_id] and user_data[user_id]["questions"] and user_data[user_id].get("current_question", 0) == 0:
        questions = user_data[user_id]["questions"]
    else:
        loading_msg = await message.answer(f"🤖 DeepSeek AI savollar tayyorlamoqda...\n📚 {subject_name}", reply_markup=cancel_menu)
        questions = await generate_questions_with_deepseek(subject, age, 30)
        if not questions:
            await loading_msg.edit_text("❌ Xatolik. Qayta urinib ko'ring.")
            await message.answer("Menyu:", reply_markup=get_main_menu(message.from_user.username, message.from_user.id))
            await state.clear()
            return
            
        user_data[user_id]["questions"] = questions
        await loading_msg.delete()
        
    user_data[user_id]["questions"] = shuffle_options(user_data[user_id]["questions"])
    user_data[user_id]["current_question"] = 0
    user_data[user_id]["correct_answers"] = 0
    user_data[user_id]["consecutive_timeouts"] = 0 
    user_data[user_id]["start_quiz_time"] = time.time()
    user_data[user_id]["subject"] = subject
    
    await state.set_state(QuizStates.answering)
    await message.answer(f"🚀 Quiz boshlanmoqda!\n📝 {len(questions)} ta savol", reply_markup=quiz_menu)
    await send_quiz_poll(message, user_id)





@dp.message(F.text.in_({"🛑 Quizni tugatish", "❌ Bekor qilish"}))
async def stop_quiz_handler(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    if user_id in user_data:
        data = user_data[user_id]
        correct = data.get("correct_answers", 0)
        current = data.get("current_question", 0)
        
        # Save result if they stop mid-quiz
        if current > 0:
            time_spent = int(time.time() - data.get("start_quiz_time", time.time()))
            subject = data.get("subject", "Noma'lum")
            save_user_result(
                user_id, 
                message.from_user.full_name, 
                message.from_user.username, 
                correct,
                current,
                time_spent,
                subject
            )
            await message.answer(f"🛑 Quiz tugatildi!\n✅ {correct}/{current}", reply_markup=get_main_menu(message.from_user.username, message.from_user.id))
        else:
            await message.answer("❌ Bekor qilindi.", reply_markup=get_main_menu(message.from_user.username, message.from_user.id))
    else:
        await message.answer("Menyu:", reply_markup=get_main_menu(message.from_user.username, message.from_user.id))
    await state.clear()

@dp.message(F.text == "🗣️ Murojatlar")
async def murojatlar_handler(message: Message) -> None:
    await message.answer(
        "🗣️ <b>Murojatlar va takliflar uchun guruhimizga qo'shiling:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👥 Guruhga qo'shilish", url="https://t.me/quizbotgroup1")]
        ])
    )

@dp.message(F.text == "📊 Natijalarim")
async def user_results_handler(message: Message):
    user_id = str(message.from_user.id)
    data = get_data()
    results = data.get("results", {})
    
    if user_id not in results:
        await message.answer("❌ Sizda hali natijalar yo'q. Quiz boshlang!")
        return
    
    r = results[user_id]
    total_correct = r.get("total_correct", 0)
    total_questions = r.get("total_questions", 0)
    total_time = r.get("total_time", 0)
    quizzes_count = r.get("quizzes_count", 0)
    
    # Calculate averages
    avg_time_per_question = total_time / total_questions if total_questions > 0 else 0
    total_incorrect = total_questions - total_correct
    
    # Format time
    h = total_time // 3600
    m = (total_time % 3600) // 60
    s = total_time % 60
    time_str = f"{h}s {m}d {s}s" if h > 0 else f"{m}d {s}s"
    
    caption = (
        f"📊 <b>Sizning umumiy natijalaringiz:</b>\n\n"
        f"📝 Yakunlangan quizlar: <b>{quizzes_count}</b> ta\n"
        f"✅ To'g'ri javoblar: <b>{total_correct}</b> ta\n"
        f"❌ Noto'g'ri javoblar: <b>{total_incorrect}</b> ta\n"
        f"⏱️ Umumiy sarflangan vaqt: <b>{time_str}</b>\n"
        f"⚡ O'rtacha bir savolga: <b>{avg_time_per_question:.1f}</b> soniya\n\n"
        f"🏆 Eng yaxshi natija: <b>{r.get('best_score', 0)}</b> ball"
    )
    
    await message.answer(caption)

@dp.message(F.text == "⬅️ Ortga")
async def back_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("📋 Menyu:", reply_markup=get_main_menu(message.from_user.username, message.from_user.id))

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

# ============ ADMIN HANDLERS ============

@dp.message(F.text == "🔑 Admen")
async def admin_handler(message: Message):
    is_admin = (message.from_user.id == ADMIN_ID) or \
               (message.from_user.username and message.from_user.username.lower() == ADMIN_USERNAME.lower())
    
    if not is_admin:
        return
    await message.answer("🔑 <b>Admin paneliga xush kelibsiz!</b>", reply_markup=admin_menu)

@dp.message(F.text == "📊 Natijalar")
async def admin_results_handler(message: Message):
    if message.from_user.username != ADMIN_USERNAME:
        return
    
    data = get_data()
    results = data.get("results", {})
        
    if not results:
        await message.answer("❌ Hali hech qanday natija yo'q.")
        return
        
    # Sort by best_score descending
    sorted_results = sorted(results.items(), key=lambda x: x[1]["best_score"], reverse=True)
    
    res_text = "🏆 <b>Barcha foydalanuvchilar natijalari:</b>\n\n"
    for i, (uid, res) in enumerate(sorted_results, 1):
        username_link = f'<a href="tg://user?id={uid}">{res["name"]}</a>'
        username_text = f" (@{res['username']})" if res.get('username') else ""
        date_text = f" | 📅 {res.get('date', 'N/A')}"
        res_text += (
            f"{i}. {username_link}{username_text}\n"
            f"   🆔 <code>{uid}</code> | 🏅 <b>{res['best_score']}</b> ball{date_text}\n\n"
        )
    
    res_text += f"📋 Jami: <b>{len(results)}</b> ta foydalanuvchi natija topshirgan"
    await message.answer(res_text, disable_web_page_preview=True)

@dp.message(F.text == "📈 Reyting")
async def admin_rating_handler(message: Message):
    if message.from_user.username != ADMIN_USERNAME:
        return
    
    loading_msg = await message.answer("📊 Statistika yuklanmoqda...")
    
    try:
        data = get_data()
        users_data = data.get("users", {})
        stats = data.get("stats", {})
        
        # --- Data Prep ---
        total_users = len(users_data)
        deepseek_total = stats.get("deepseek_calls", 0)
        durations = stats.get("ai_durations", [0])
        avg_speed = sum(durations) / len(durations) if durations else 0
        error_count = len(stats.get("errors", []))
        
        # Last 7 days growth
        from datetime import timedelta
        dates, counts = [], []
        for i in range(6, -1, -1):
            day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            day_count = sum(1 for u in users_data.values() if u.get("joined_date", "").startswith(day))
            dates.append(day[5:])
            counts.append(day_count)
            
        # --- Plotting ---
        plt.style.use('dark_background')
        fig = plt.figure(figsize=(12, 14))
        gs = fig.add_gridspec(3, 2, height_ratios=[1, 1, 0.6])
        
        # 1. User Growth
        ax1 = fig.add_subplot(gs[0, :])
        bars = ax1.bar(dates, counts, color='#00ccff', alpha=0.8)
        ax1.set_title('So\'nggi 7 kunlik o\'sish', fontsize=16, pad=20)
        ax1.bar_label(bars, padding=3)
        
        # 2. AI Speed & Performance
        ax2 = fig.add_subplot(gs[1, 0])
        labels = ['O\'rtacha Tezlik (sek)', 'Jami AI So\'rovlar']
        values = [avg_speed, deepseek_total / 10 if deepseek_total > 0 else 0] # Scaled for visibility
        ax2.bar(labels, [avg_speed, deepseek_total], color=['#ff9900', '#ff0055'])
        ax2.set_title('AI Ko\'rsatkichlari', fontsize=14)
        
        # 3. Success vs Errors
        ax3 = fig.add_subplot(gs[1, 1])
        success = deepseek_total
        ax3.pie([success, error_count], labels=['Muvaffaqiyat', 'Xatolik'], 
                autopct='%1.1f%%', colors=['#00ff88', '#ff3333'], startangle=90)
        ax3.set_title('Muvaffaqiyatli so\'rovlar', fontsize=14)

        # 4. Detailed Stats Table/Text
        ax4 = fig.add_subplot(gs[2, :])
        server_info = get_server_info()
        stats_text = (
            f"SYSTEM STATUS\n"
            f"OS: {server_info['os'][:30]}\n"
            f"Python: {server_info['python']} | Uptime: {server_info['uptime']}\n"
            f"AI Avg Speed: {avg_speed:.2f}s | Errors: {error_count}\n"
            f"Last Activity: {stats.get('last_call', 'N/A')}"
        )
        ax4.text(0.5, 0.5, stats_text, ha='center', va='center', fontsize=13,
                 bbox=dict(boxstyle="round,pad=1.5", fc="#1e1e1e", ec="#00ccff", alpha=1))
        ax4.axis('off')
        
        # Save
        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format='png', dpi=100, facecolor='#121212')
        buf.seek(0)
        plt.close(fig)
        
        await loading_msg.delete()
        await message.answer_photo(
            photo=BufferedInputFile(buf.read(), filename="stats.png"),
            caption=f"📈 <b>Bot Reytingi va Holati</b>\n\nAI Tezligi: <b>{avg_speed:.2f}s</b>"
        )
        
    except Exception as e:
        track_error(f"Rating chart error: {e}")
        await loading_msg.edit_text(f"❌ Xatolik yuz berdi.")

@dp.message(F.text == "📊 Natijalar")
async def admin_results_handler(message: Message):
    if message.from_user.username != ADMIN_USERNAME:
        return
    
    data = get_data()
    results = data.get("results", {})
    
    if not results:
        await message.answer("❌ Natijalar yo'q.")
        return

    # Sort top 5 by total_correct
    top_5 = sorted(results.items(), key=lambda x: x[1].get("total_correct", 0), reverse=True)[:5]
    
    try:
        plt.figure(figsize=(10, 6), facecolor='#121212')
        ax = plt.subplot(111)
        ax.axis('off')
        
        columns = ["Nomi", "Fanlar", "To'g'ri", "Vaqt", "Quizlar"]
        table_data = []
        
        for uid, r in top_5:
            subjects = ", ".join(r.get("subjects", []))[:20]
            if len(", ".join(r.get("subjects", []))) > 20: subjects += "..."
            
            # Format time
            ts = r.get("total_time", 0)
            time_str = f"{ts//60}m {ts%60}s"
            
            table_data.append([
                r.get("name", "Noma'lum")[:15],
                subjects or "N/A",
                r.get("total_correct", 0),
                time_str,
                r.get("quizzes_count", 0)
            ])
            
        the_table = ax.table(cellText=table_data, colLabels=columns, loc='center', cellLoc='center')
        the_table.auto_set_font_size(False)
        the_table.set_fontsize(12)
        the_table.scale(1.2, 2.5)
        
        # Styling
        for (row, col), cell in the_table.get_celld().items():
            cell.set_text_props(color='white')
            if row == 0:
                cell.set_facecolor('#00ccff')
                cell.set_text_props(weight='bold', color='black')
            else:
                cell.set_facecolor('#1e1e1e')
        
        plt.title("TOP 5 FOYDALANUVCHILAR", color='#00ccff', fontsize=18, pad=20)
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100, facecolor='#121212')
        buf.seek(0)
        plt.close()
        
        await message.answer_photo(
            photo=BufferedInputFile(buf.read(), filename="top5.png"),
            caption="📊 <b>Eng yaxshi 5 foydalanuvchi natijalari</b>"
        )
    except Exception as e:
        track_error(f"Results table error: {e}")
        await message.answer(f"❌ Jadval yaratishda xatolik.")

@dp.message(F.text == "👥 Obunchilar")
async def admin_subscribers_handler(message: Message):
    if message.from_user.username != ADMIN_USERNAME:
        return
    
    data = get_data()
    users = data.get("users", {})
    
    if not users:
        await message.answer("❌ Hali hech qanday obunachi yo'q.")
        return
    
    total = len(users)
    
    # Sort by join date (newest first)
    sorted_users = sorted(users.items(), key=lambda x: x[1].get("joined_date", ""), reverse=True)
    
    # Paginate - send in chunks of 10 to include photos
    chunk_size = 10
    chunks = [sorted_users[i:i + chunk_size] for i in range(0, len(sorted_users), chunk_size)]
    
    for page_num, chunk in enumerate(chunks, 1):
        await message.answer(f"👥 <b>Obunchilar</b> (sahifa {page_num}/{len(chunks)})\n📊 Jami: <b>{total}</b> ta")
        
        for uid, user_info in chunk:
            full_name = user_info.get("full_name", "Noma'lum")
            first_name = user_info.get("first_name", "")
            last_name = user_info.get("last_name", "")
            username = user_info.get("username", "")
            joined = user_info.get("joined_date", "N/A")
            photo_path = user_info.get("photo")
            
            user_link = f'<a href="tg://user?id={uid}">{full_name}</a>'
            username_text = f" (@{username})" if username else ""
            name_parts = f"{first_name} {last_name}".strip()
            
            caption = (
                f"👤 <b>Akkaunt:</b> {user_link}\n"
                f"   👤 <b>Username:</b> {username_text}\n"
                f"   📅 <b>Qo'shilgan:</b> {joined}"
            )
            
            try:
                if photo_path and os.path.exists(photo_path):
                    from aiogram.types import FSInputFile
                    photo = FSInputFile(photo_path)
                    await message.answer_photo(photo, caption=caption)
                else:
                    await message.answer(caption)
            except Exception as e:
                logging.error(f"Error sending photo for user {uid}: {e}")
                await message.answer(caption)
        
        await asyncio.sleep(0.5) # Avoid flood limits

@dp.message()
async def echo_handler(message: Message) -> None:
    await message.answer("🤔 Menyudan tanlang.", reply_markup=get_main_menu(message.from_user.username, message.from_user.id))

async def main() -> None:
    # Drop pending updates when the bot starts
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
