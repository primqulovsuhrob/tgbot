import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize bot and dispatcher
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    exit("Error: BOT_TOKEN not found in .env file")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Handler for /start command
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = [
        [
            types.KeyboardButton(text="Quiz yaratish"),
            types.KeyboardButton(text="Quiz boshlash")
        ],
        [
            types.KeyboardButton(text="Guruhga qo'shilish")
        ]
    ]
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True,
        input_field_placeholder="Tanlang..."
    )
    await message.answer("Salom botimga hush kelibsiz", reply_markup=keyboard)

# Handler for /help command
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer("Yordam: \n/start - Botni ishga tushirish\n/help - Yordam")

# Handler for "Quiz yaratish"
@dp.message(lambda message: message.text == "Quiz yaratish")
async def create_quiz_handler(message: types.Message):
    kb = [
        [
            types.KeyboardButton(text="Mavzu tanlash"),
            types.KeyboardButton(text="Fayl tashlash")
        ],
        [
            types.KeyboardButton(text="Orqaga ⬅️")
        ]
    ]
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=kb,
        resize_keyboard=True,
        input_field_placeholder="Tanlang..."
    )
    await message.answer("Quiz yaratish usulini tanlang:", reply_markup=keyboard)

# Handler for "Mavzu tanlash"
@dp.message(lambda message: message.text == "Mavzu tanlash")
async def select_topic_handler(message: types.Message):
    await message.answer("Mavzuni tanlang (hozircha mavzular yo'q).")

# Handler for "Fayl tashlash"
@dp.message(lambda message: message.text == "Fayl tashlash")
async def upload_file_handler(message: types.Message):
    await message.answer("Iltimos, faylni yuklang.")

# Handler for "Orqaga"
@dp.message(lambda message: message.text == "Orqaga ⬅️")
async def back_handler(message: types.Message):
    await cmd_start(message)

# Handler for "Quiz boshlash"
@dp.message(lambda message: message.text == "Quiz boshlash")
async def start_quiz_handler(message: types.Message):
    await message.answer("Quiz boshlash uchun ID kiriting yoki ro'yxatdan tanlang.")

# Handler for "Guruhga qo'shilish"
@dp.message(lambda message: message.text == "Guruhga qo'shilish")
async def join_group_handler(message: types.Message):
    ikb = [
        [
            types.InlineKeyboardButton(text="Guruhga kirish ➡️", url="https://t.me/quizbotgroup1")
        ]
    ]
    inline_keyboard = types.InlineKeyboardMarkup(inline_keyboard=ikb)
    await message.answer("Guruhga qo'shilish uchun quyidagi tugmani bosing:", reply_markup=inline_keyboard)

# Echo handler
@dp.message()
async def echo_handler(message: types.Message):
    try:
        await message.answer(f"Siz yozdingiz: {message.text}")
    except TypeError:
        await message.answer("Kechirasiz, men hozircha faqat matnli xabarlarni tushunaman.")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
