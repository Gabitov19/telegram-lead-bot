import asyncio
import logging
import time
import os
import datetime
from dotenv import load_dotenv
import aiosqlite  # <--- Новая библиотека для БД

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton
)

# Загружаем переменные из файла .env
load_dotenv()

# === НАСТРОЙКИ ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
SPAM_DELAY = 60
DB_NAME = "leads.db" # Имя файла базы данных

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

last_request_time: dict[int, float] = {}


# === РАБОТА С БАЗОЙ ДАННЫХ ===
async def init_db():
    """Создает таблицу, если её еще нет"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT,
                contact TEXT,
                username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def save_lead(user_id: int, name: str, contact: str, username: str):
    """Сохраняет новую заявку в базу"""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO leads (user_id, name, contact, username) VALUES (?, ?, ?, ?)",
            (user_id, name, contact, username)
        )
        await db.commit()

async def get_last_leads(limit: int = 10):
    """Достает последние N заявок"""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row 
        async with db.execute(
            "SELECT name, contact, username, created_at FROM leads ORDER BY id DESC LIMIT ?", 
            (limit,)
        ) as cursor:
            return await cursor.fetchall()


# === СОСТОЯНИЯ ===
class LeadForm(StatesGroup):
    waiting_name = State()
    waiting_phone = State()


# === КЛАВИАТУРЫ ===
def get_phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить телефон", request_contact=True)],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🏠 Главное меню")]],
        resize_keyboard=True,
        is_persistent=True
    )


# === СТАРТ ===
@dp.message(Command("start"))
@dp.message(F.text == "🏠 Главное меню")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    inline_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Прайс-лист", callback_data="price")],
        [InlineKeyboardButton(text="✍️ Оставить заявку", callback_data="request")],
    ])
    
    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\n"
        "Я — демо-бот.",
        reply_markup=get_main_menu_keyboard()
    )
    await message.answer("Выберите действие:", reply_markup=inline_keyboard)


# === КОМАНДА ДЛЯ АДМИНА: /leads ===
@dp.message(Command("leads"))
async def show_leads(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав для этой команды.")
        return
        
    leads = await get_last_leads(limit=10)
    
    if not leads:
        await message.answer("База данных пуста. Заявок пока нет.")
        return
        
    text = "📊 <b>Последние 10 заявок:</b>\n\n"
    for i, lead in enumerate(leads, 1):
        date_str = lead['created_at'].split('.')[0] if '.' in lead['created_at'] else lead['created_at']
        text += (
            f"{i}. 👤 <b>{lead['name']}</b>\n"
            f"📞 {lead['contact']}\n"
            f"💬 @{lead['username']}\n"
            f"🕒 {date_str}\n\n"
        )
    
    await message.answer(text, parse_mode="HTML")


# === ПРАЙС ===
@dp.callback_query(F.data == "price")
async def show_price(callback: CallbackQuery):
    await callback.message.answer(
        "💰 Услуги:\n"
        "• Консультация — 1000 ₽\n"
        "• Разработка бота — от 15000 ₽\n"
        "• Поддержка — 5000 ₽/мес"
    )
    await callback.answer()


# === НАЧАЛО ЗАЯВКИ ===
@dp.callback_query(F.data == "request")
async def request_start(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    now = time.monotonic()
    elapsed = now - last_request_time.get(user_id, 0)

    if elapsed < SPAM_DELAY:
        wait = int(SPAM_DELAY - elapsed)
        await callback.answer(f"Слишком часто. Подождите {wait} сек.", show_alert=True)
        return

    last_request_time[user_id] = now
    await state.set_state(LeadForm.waiting_name)
    await callback.message.answer("Отлично! Как вас зовут?")
    await callback.answer()


# === ШАГ 1: ИМЯ ===
@dp.message(LeadForm.waiting_name, F.text)
async def process_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2:
        await message.answer("Имя слишком короткое. Напишите ещё раз.")
        return

    await state.update_data(name=name)
    await state.set_state(LeadForm.waiting_phone)
    
    await message.answer(
        f"Приятно познакомиться, {name}! 👋\n"
        "Теперь нажмите кнопку ниже, чтобы поделиться номером, или напишите его вручную.",
        reply_markup=get_phone_keyboard()
    )


# === ОТМЕНА ЗАЯВКИ ===
@dp.message(LeadForm.waiting_phone, F.text == "❌ Отмена")
@dp.message(LeadForm.waiting_name, F.text == "❌ Отмена")
async def cancel_form(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Форма отменена. Если захотите начать заново — нажмите /start.",
        reply_markup=get_main_menu_keyboard()
    )


# === ШАГ 2: ТЕЛЕФОН + СОХРАНЕНИЕ В БД ===
@dp.message(LeadForm.waiting_phone, F.contact | F.text)
async def process_phone(message: Message, state: FSMContext):
    if message.contact:
        contact = message.contact.phone_number
    else:
        contact = message.text.strip()
        if len(contact) < 5:
            await message.answer("Похоже, это не контакт. Напишите телефон или @username.")
            return

    data = await state.get_data()
    name = data.get("name", "—")
    username = message.from_user.username or "нет"
    user_id = message.from_user.id

    # 1. СОХРАНЯЕМ В БАЗУ ДАННЫХ
    await save_lead(user_id, name, contact, username)

    # 2. ОТПРАВЛЯЕМ УВЕДОМЛЕНИЕ АДМИНУ
    lead_text = (
        "🔔 <b>Новая заявка!</b>\n\n"
        f"👤 Имя: {name}\n"
        f"📞 Контакт: {contact}\n"
        f"💬 Telegram: @{username} (id: {user_id})"
    )

    try:
        await bot.send_message(ADMIN_ID, lead_text, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при отправке админу: {e}")
        logging.error(f"Не удалось отправить заявку: {e}")

    await message.answer(
        f"Спасибо, {name}! Заявка принята и сохранена. 🙌\n"
        "Я свяжусь с вами в ближайшее время.",
        reply_markup=get_main_menu_keyboard()
    )
    await state.clear()


# === ЗАГЛУШКА ===
@dp.message(F.text)
async def fallback(message: Message):
    await message.answer(
        "Я вас не понял. Нажмите кнопку ниже или введите /start.", 
        reply_markup=get_main_menu_keyboard()
    )


# === ЗАПУСК ===
async def main():
    await init_db() # Инициализируем базу данных
    print("База данных готова.")
    print("Бот запущен. Нажмите Ctrl+C для остановки.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())