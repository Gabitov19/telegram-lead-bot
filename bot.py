import asyncio
import logging
import time
import os
import datetime
import aiosqlite
from dotenv import load_dotenv
from openpyxl import Workbook
from aiogram.types import FSInputFile, BotCommand

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton
)

load_dotenv()

# === НАСТРОЙКИ ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
SPAM_DELAY = 60
DB_NAME = "leads.db"
MANAGER_USERNAME = "gabitov19" # <--- ЗАМЕНИТЕ НА ВАШ ЮЗЕРНЕЙМ (без @)

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

last_request_time: dict[int, float] = {}


# === БАЗА ДАННЫХ ===
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица заявок
        await db.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, name TEXT, contact TEXT, username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Таблица пользователей (для рассылки)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def save_user(user_id: int, username: str, first_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)",
            (user_id, username, first_name)
        )
        await db.commit()

async def save_lead(user_id: int, name: str, contact: str, username: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO leads (user_id, name, contact, username) VALUES (?, ?, ?, ?)",
            (user_id, name, contact, username)
        )
        await db.commit()

async def get_last_leads(limit: int = 10):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row 
        async with db.execute("SELECT name, contact, username, created_at FROM leads ORDER BY id DESC LIMIT ?", (limit,)) as cursor:
            return await cursor.fetchall()

async def get_all_leads_for_excel():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, name, contact, username, created_at FROM leads ORDER BY id ASC") as cursor:
            return await cursor.fetchall()

async def get_stats():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            users_count = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM leads") as cursor:
            leads_count = (await cursor.fetchone())[0]
        # Заявки за сегодня
        today = datetime.date.today().isoformat()
        async with db.execute("SELECT COUNT(*) FROM leads WHERE date(created_at) = ?", (today,)) as cursor:
            today_leads = (await cursor.fetchone())[0]
    return users_count, leads_count, today_leads

async def get_all_user_ids():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]


# === СОСТОЯНИЯ ===
class Form(StatesGroup):
    waiting_name = State()
    waiting_phone = State()
    waiting_feedback = State()
    waiting_broadcast = State()


# === КЛАВИАТУРЫ ===
def get_phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить телефон", request_contact=True)], [KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True, one_time_keyboard=True
    )

def get_main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🏠 Главное меню")]],
        resize_keyboard=True, is_persistent=True
    )

def get_inline_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Прайс-лист", callback_data="price")],
        [InlineKeyboardButton(text="✍️ Оставить заявку", callback_data="request")],
        [InlineKeyboardButton(text="❓ FAQ", callback_data="faq"), InlineKeyboardButton(text="💬 Менеджер", callback_data="contact")],
        [InlineKeyboardButton(text="📝 Оставить отзыв", callback_data="feedback")]
    ])


# === СТАРТ ===
@dp.message(Command("start"))
@dp.message(F.text == "🏠 Главное меню")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await save_user(message.from_user.id, message.from_user.username or "", message.from_user.first_name)
    
    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\nЯ — демо-бот.",
        reply_markup=get_main_menu_keyboard()
    )
    await message.answer("Выберите действие:", reply_markup=get_inline_menu())


# === FAQ И МЕНЕДЖЕР ===
@dp.callback_query(F.data == "faq")
async def show_faq(callback: CallbackQuery):
    text = "❓ <b>Часто задаваемые вопросы:</b>\n\n<b>Вопрос 1:</b> Как работает бот?\n<i>Ответ: Вы оставляете заявку, мы связываемся с вами.</i>\n\n<b>Вопрос 2:</b> Сколько стоит?\n<i>Ответ: Смотрите раздел «Прайс-лист».</i>"
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "contact")
async def show_contact(callback: CallbackQuery):
    text = f"💬 <b>Связь с менеджером</b>\n\nНажмите на кнопку ниже, чтобы написать нам напрямую."
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Написать менеджеру", url=f"https://t.me/{MANAGER_USERNAME}")]
    ])
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


# === ОБРАТНАЯ СВЯЗЬ ===
@dp.callback_query(F.data == "feedback")
async def start_feedback(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_feedback)
    await callback.message.answer("📝 Напишите ваш отзыв или пожелание одним сообщением:")
    await callback.answer()

@dp.message(Form.waiting_feedback, F.text)
async def process_feedback(message: Message, state: FSMContext):
    text = f"📩 <b>Новый отзыв!</b>\n\nОт: @{message.from_user.username or 'нет'} (id: {message.from_user.id})\n\nТекст:\n<i>{message.text}</i>"
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
    except Exception:
        pass
    await message.answer("Спасибо за ваш отзыв! 🙌", reply_markup=get_main_menu_keyboard())
    await state.clear()


# === ПРАЙС ===
@dp.callback_query(F.data == "price")
async def show_price(callback: CallbackQuery):
    await callback.message.answer("💰 Услуги:\n• Консультация — 1000 ₽\n• Разработка бота — от 15000 ₽\n• Поддержка — 5000 ₽/мес")
    await callback.answer()


# === ЗАЯВКА ===
@dp.callback_query(F.data == "request")
async def request_start(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    now = time.monotonic()
    if now - last_request_time.get(user_id, 0) < SPAM_DELAY:
        await callback.answer("Слишком часто. Подождите.", show_alert=True)
        return
    last_request_time[user_id] = now
    await state.set_state(Form.waiting_name)
    await callback.message.answer("Отлично! Как вас зовут?")
    await callback.answer()

@dp.message(Form.waiting_name, F.text)
async def process_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2:
        await message.answer("Имя слишком короткое. Напишите ещё раз.")
        return
    await state.update_data(name=name)
    await state.set_state(Form.waiting_phone)
    await message.answer(f"Приятно познакомиться, {name}! 👋\nНажмите кнопку ниже, чтобы поделиться номером.", reply_markup=get_phone_keyboard())

@dp.message(Form.waiting_phone, F.contact | F.text)
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
    
    await save_lead(message.from_user.id, name, contact, username)
    
    lead_text = f"🔔 <b>Новая заявка!</b>\n\n👤 Имя: {name}\n📞 Контакт: {contact}\n💬 Telegram: @{username}"
    try:
        await bot.send_message(ADMIN_ID, lead_text, parse_mode="HTML")
    except Exception:
        pass

    await message.answer(f"Спасибо, {name}! Заявка принята. 🙌", reply_markup=get_main_menu_keyboard())
    await state.clear()

@dp.message(F.text == "❌ Отмена")
async def cancel_form(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=get_main_menu_keyboard())


# === АДМИН-ПАНЕЛЬ: СТАТИСТИКА ===
@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != ADMIN_ID: return
    users, leads, today = await get_stats()
    text = f"📊 <b>Статистика бота:</b>\n\n👥 Пользователей: {users}\n📝 Всего заявок: {leads}\n🔥 Заявок за сегодня: {today}"
    await message.answer(text, parse_mode="HTML")


# === АДМИН-ПАНЕЛЬ: РАССЫЛКА ===
@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.set_state(Form.waiting_broadcast)
    await message.answer("📢 Отправьте сообщение для рассылки (можно с фото):")

@dp.message(Form.waiting_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    
    users = await get_all_user_ids()
    if not users:
        await message.answer("Нет пользователей для рассылки.")
        await state.clear()
        return

    success = 0
    fail = 0
    status_msg = await message.answer(f"⏳ Начинаю рассылку для {len(users)} чел...")
    
    for user_id in users:
        try:
            await message.copy_to(chat_id=user_id) # Копирует сообщение как есть
            success += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05) # Защита от блокировки Telegram (лимит 30 сообщений в секунду)

    await status_msg.edit_text(f"✅ Рассылка завершена!\n\nДоставлено: {success}\nНе доставлено (заблокировали): {fail}")
    await state.clear()


# === АДМИН-ПАНЕЛЬ: LEADS & EXPORT ===
@dp.message(Command("leads"))
async def show_leads(message: Message):
    if message.from_user.id != ADMIN_ID: return
    leads = await get_last_leads(10)
    if not leads:
        await message.answer("База пуста.")
        return
    text = "📊 <b>Последние 10 заявок:</b>\n\n"
    for i, lead in enumerate(leads, 1):
        date_str = lead['created_at'].split('.')[0] if '.' in lead['created_at'] else lead['created_at']
        text += f"{i}. 👤 <b>{lead['name']}</b>\n📞 {lead['contact']}\n💬 @{lead['username']}\n🕒 {date_str}\n\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("export"))
async def export_to_excel(message: Message):
    if message.from_user.id != ADMIN_ID: return
    leads = await get_all_leads_for_excel()
    if not leads:
        await message.answer("В базе пока нет заявок.")
        return

    wb = Workbook()
    ws = wb.active
    ws.title = "Заявки"
    ws.append(["ID", "Имя", "Контакт", "Telegram", "Дата"])
    for lead in leads:
        ws.append(list(lead))
    for cell in ws[1]:
        cell.font = cell.font.copy(bold=True)

    filename = "leads_export.xlsx"
    wb.save(filename)

    await message.answer("📥 Формирую файл Excel...")
    document = FSInputFile(filename)
    await message.answer_document(document, caption="📊 Все заявки в одном файле!")
    os.remove(filename)


# === ЗАПУСК ===
async def main():
    await init_db()
    
    await bot.set_my_commands([
        BotCommand(command="start", description="🏠 Главное меню"),
        BotCommand(command="leads", description="📊 Последние заявки (Админ)"),
        BotCommand(command="export", description="📥 Выгрузить в Excel (Админ)"),
        BotCommand(command="stats", description="📈 Статистика (Админ)"),
        BotCommand(command="broadcast", description="📢 Сделать рассылку (Админ)"),
    ])
    
    print("База данных готова.")
    print("Бот запущен.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())