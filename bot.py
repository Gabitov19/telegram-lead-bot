import asyncio
import logging
import time
import os
import datetime
import json
import aiosqlite
from dotenv import load_dotenv
from openpyxl import Workbook
from aiogram.types import FSInputFile, BotCommand, WebAppInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton
)

# Импортируем наш словарь переводов
from texts import get_text

# Загружаем настройки из .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
MANAGER_USERNAME = os.getenv("MANAGER_USERNAME")
WEBAPP_URL = os.getenv("WEBAPP_URL")
GAME_URL = os.getenv("GAME_URL") # Ссылка на игру

DB_NAME = "leads.db"
SPAM_DELAY = 60

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

last_request_time: dict[int, float] = {}

# ==========================================
# СОСТОЯНИЯ
# ==========================================
class Form(StatesGroup):
    waiting_name = State()
    waiting_phone = State()
    waiting_feedback = State()
    waiting_broadcast = State()
    waiting_manager_id = State()

# ==========================================
# БАЗА ДАННЫХ
# ==========================================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                user_id INTEGER, name TEXT, contact TEXT, username TEXT, 
                service TEXT, appointment_date TEXT, appointment_time TEXT, created_at TEXT
            )
        """)
        # Миграция для старых баз
        try:
            await db.execute("ALTER TABLE leads ADD COLUMN service TEXT")
            await db.execute("ALTER TABLE leads ADD COLUMN appointment_date TEXT")
            await db.execute("ALTER TABLE leads ADD COLUMN appointment_time TEXT")
        except Exception: pass 

        await db.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, joined_at TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS managers (user_id INTEGER PRIMARY KEY, added_at TEXT)")
        await db.commit()

async def save_user(user_id: int, username: str, first_name: str):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, joined_at) VALUES (?, ?, ?, ?)", (user_id, username, first_name, now))
        await db.commit()

async def save_lead(user_id: int, name: str, contact: str, username: str, service: str = "—", app_date: str = "—", app_time: str = "—"):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO leads (user_id, name, contact, username, service, appointment_date, appointment_time, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (user_id, name, contact, username, service, app_date, app_time, now))
        await db.commit()

async def get_last_leads(limit: int = 10):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row 
        async with db.execute("SELECT name, contact, username, service, appointment_date, appointment_time, created_at FROM leads ORDER BY id DESC LIMIT ?", (limit,)) as cursor:
            return await cursor.fetchall()

async def get_all_leads_for_excel():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, name, contact, username, service, appointment_date, appointment_time, created_at FROM leads ORDER BY id ASC") as cursor:
            return await cursor.fetchall()

async def get_stats():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor: users_count = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM leads") as cursor: leads_count = (await cursor.fetchone())[0]
        today = datetime.date.today().isoformat()
        async with db.execute("SELECT COUNT(*) FROM leads WHERE date(created_at) = ?", (today,)) as cursor: today_leads = (await cursor.fetchone())[0]
    return users_count, leads_count, today_leads

async def get_all_user_ids():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            return [row[0] for row in await cursor.fetchall()]

async def add_manager(user_id: int):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO managers (user_id, added_at) VALUES (?, ?)", (user_id, now))
        await db.commit()

async def remove_manager(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM managers WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_managers() -> list[int]:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM managers") as cursor:
            return [row[0] for row in await cursor.fetchall()]

async def clear_leads_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM leads")
        await db.execute("DELETE FROM sqlite_sequence WHERE name='leads'")
        await db.commit()

# ==========================================
# КЛАВИАТУРЫ
# ==========================================
def get_phone_keyboard(lang: str):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить телефон" if lang.startswith("ru") else "📱 Share Phone", request_contact=True)], [KeyboardButton(text="❌ Отмена" if lang.startswith("ru") else "❌ Cancel")]],
        resize_keyboard=True, one_time_keyboard=True
    )

def get_main_menu_keyboard(lang: str):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=get_text(lang, "btn_webapp"), web_app=WebAppInfo(url=WEBAPP_URL))],
            [KeyboardButton(text=get_text(lang, "btn_main_menu"))]
        ],
        resize_keyboard=True, is_persistent=True
    )

def get_inline_menu(lang: str, user_id: int):
    github_url = "https://github.com/Gabitov19/telegram-lead-bot" 
    keyboard = [
        [InlineKeyboardButton(text=get_text(lang, "btn_game"), web_app=WebAppInfo(url=GAME_URL))], # Кнопка игры
        [InlineKeyboardButton(text=get_text(lang, "btn_portfolio"), url=github_url)],
        [InlineKeyboardButton(text=get_text(lang, "btn_price"), callback_data="price")],
        [InlineKeyboardButton(text=get_text(lang, "btn_faq"), callback_data="faq"), InlineKeyboardButton(text=get_text(lang, "btn_manager"), callback_data="contact")],
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton(text=get_text(lang, "btn_admin"), callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_admin_panel(lang: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(lang, "btn_manage_team"), callback_data="manage_managers")],
        [InlineKeyboardButton(text=get_text(lang, "btn_stats"), callback_data="admin_stats")],
        [InlineKeyboardButton(text=get_text(lang, "btn_broadcast"), callback_data="admin_broadcast")],
        [InlineKeyboardButton(text=get_text(lang, "btn_clear_db"), callback_data="confirm_clear_db")],
        [InlineKeyboardButton(text=get_text(lang, "btn_back_main"), callback_data="back_to_main")]
    ])

def get_managers_menu(lang: str, managers: list):
    keyboard = [[InlineKeyboardButton(text=get_text(lang, "btn_add_mgr"), callback_data="add_mgr")]]
    for mgr in managers:
        keyboard.append([InlineKeyboardButton(text=get_text(lang, "btn_del_mgr", id=mgr), callback_data=f"del_mgr:{mgr}")])
    keyboard.append([InlineKeyboardButton(text=get_text(lang, "btn_back"), callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ==========================================
# ОБРАБОТЧИКИ КЛИЕНТСКОЙ ЧАСТИ
# ==========================================
@dp.message(Command("start"))
@dp.message(F.text.in_(["🏠 Главное меню", "🏠 Main Menu"]))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    lang = message.from_user.language_code or "ru"
    await save_user(message.from_user.id, message.from_user.username or "", message.from_user.first_name)
    
    await message.answer(
        get_text(lang, "start", name=message.from_user.first_name),
        reply_markup=get_main_menu_keyboard(lang)
    )
    await message.answer(get_text(lang, "choose_action"), reply_markup=get_inline_menu(lang, message.from_user.id))

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"
    await callback.message.edit_text(get_text(lang, "choose_action"), reply_markup=get_inline_menu(lang, callback.from_user.id))
    await callback.answer()

@dp.callback_query(F.data == "price")
async def show_price(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"
    await callback.message.answer(get_text(lang, "price_text"), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "faq")
async def show_faq(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"
    await callback.message.answer(get_text(lang, "faq_text"), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "contact")
async def show_contact(callback: CallbackQuery):
    lang = callback.from_user.language_code or "ru"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=get_text(lang, "btn_write_manager"), url=f"https://t.me/{MANAGER_USERNAME}")]])
    await callback.message.answer(get_text(lang, "contact_text"), reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "feedback")
async def start_feedback(callback: CallbackQuery, state: FSMContext):
    lang = callback.from_user.language_code or "ru"
    await state.set_state(Form.waiting_feedback)
    await callback.message.answer(get_text(lang, "feedback_prompt"))
    await callback.answer()

@dp.message(Form.waiting_feedback, F.text)
async def process_feedback(message: Message, state: FSMContext):
    lang = message.from_user.language_code or "ru"
    text = f"📩 <b>Новый отзыв!</b>\n\nОт: @{message.from_user.username or 'нет'}\n\n<i>{message.text}</i>"
    try: await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
    except Exception: pass
    await message.answer(get_text(lang, "feedback_thanks"), reply_markup=get_main_menu_keyboard(lang))
    await state.clear()

@dp.message(F.text.in_(["❌ Отмена", "❌ Cancel"]))
async def cancel_form(message: Message, state: FSMContext):
    lang = message.from_user.language_code or "ru"
    await state.clear()
    await message.answer(get_text(lang, "cancel"), reply_markup=get_main_menu_keyboard(lang))

# ==========================================
# ОБРАБОТКА WEB APP (ИГРА И ЗАКАЗ)
# ==========================================
@dp.message(F.web_app_data)
async def handle_webapp_data(message: Message):
    lang = message.from_user.language_code or "ru"
    try:
        data = json.loads(message.web_app_data.data)
        
        # Если это данные из ИГРЫ
        if "score" in data:
            score = data["score"]
            username = message.from_user.username or message.from_user.first_name
            text = f"🎮 <b>Результат игры!</b>\n\nПользователь @{username} накликал <b>{score}</b> монет!"
            await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
            await message.answer(f"Отличный результат! Вы накликали {score} монет. 🙌\n\nВ реальном проекте эти монеты можно было бы обменять на скидку или приз.")
            return

        # Если это данные из ФОРМЫ ЗАКАЗА БОТА
        name = data.get("name")
        contact = data.get("phone")
        bot_type = data.get("bot_type", "Не указан")
        description = data.get("description", "Без описания")
        username = message.from_user.username or "нет"
        
        await save_lead(message.from_user.id, name, contact, username, service=bot_type, app_date="—", app_time="—")
        
        lead_text = (
            get_text(lang, "lead_title") +
            f"👤 Имя: {name}\n"
            f"📞 Контакт: {contact}\n"
            f"🤖 Тип бота: {bot_type}\n"
            f"📝 Описание: {description}\n"
            f"💬 Telegram: @{username}"
        )
        
        managers = await get_managers()
        recipients = set(managers)
        recipients.add(ADMIN_ID)
        
        for recipient_id in recipients:
            try: await bot.send_message(recipient_id, lead_text, parse_mode="HTML")
            except Exception: pass
                
        await message.answer(get_text(lang, "lead_success", name=name), reply_markup=get_main_menu_keyboard(lang))
    except Exception as e:
        logging.error(f"Ошибка Web App: {e}")
        await message.answer(get_text(lang, "lead_error"))

# ==========================================
# АДМИН-ПАНЕЛЬ
# ==========================================
@dp.callback_query(F.data == "admin_panel")
async def show_admin_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: 
        await callback.answer("Доступ запрещен / Access Denied", show_alert=True)
        return
    lang = callback.from_user.language_code or "ru"
    try:
        await callback.message.edit_text(get_text(lang, "admin_panel"), reply_markup=get_admin_panel(lang), parse_mode="HTML")
    except Exception: pass
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def btn_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    lang = callback.from_user.language_code or "ru"
    users, leads, today = await get_stats()
    text = get_text(lang, "stats_text", users=users, leads=leads, today=today)
    try:
        await callback.message.edit_text(text, reply_markup=get_admin_panel(lang), parse_mode="HTML")
    except Exception: pass
    await callback.answer()

@dp.callback_query(F.data == "admin_broadcast")
async def btn_broadcast(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    lang = callback.from_user.language_code or "ru"
    await state.set_state(Form.waiting_broadcast)
    await callback.message.answer(get_text(lang, "broadcast_prompt"))
    await callback.answer()

@dp.message(Form.waiting_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    lang = message.from_user.language_code or "ru"
    users = await get_all_user_ids()
    if not users:
        await message.answer("Нет пользователей / No users.")
        await state.clear()
        return
    success, fail = 0, 0
    status_msg = await message.answer(get_text(lang, "broadcast_start", count=len(users)))
    for user_id in users:
        try:
            await message.copy_to(chat_id=user_id)
            success += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await status_msg.edit_text(get_text(lang, "broadcast_done", success=success, fail=fail))
    await state.clear()

# --- ОЧИСТКА БАЗЫ ---
@dp.callback_query(F.data == "confirm_clear_db")
async def confirm_clear_db(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    lang = callback.from_user.language_code or "ru"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(lang, "btn_yes_delete"), callback_data="do_clear_db")],
        [InlineKeyboardButton(text=get_text(lang, "btn_no_cancel"), callback_data="admin_panel")]
    ])
    await callback.message.edit_text(get_text(lang, "clear_db_warning"), reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "do_clear_db")
async def do_clear_db(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    lang = callback.from_user.language_code or "ru"
    await clear_leads_db()
    await callback.message.edit_text(get_text(lang, "clear_db_success"), reply_markup=get_admin_panel(lang), parse_mode="HTML")
    await callback.answer("База очищена / DB Cleared")

# --- УПРАВЛЕНИЕ КОМАНДОЙ ---
@dp.callback_query(F.data == "manage_managers")
async def btn_manage_managers(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    lang = callback.from_user.language_code or "ru"
    managers = await get_managers()
    text = get_text(lang, "team_title")
    text += get_text(lang, "team_has") if managers else get_text(lang, "team_empty")
    await callback.message.edit_text(text, reply_markup=get_managers_menu(lang, managers), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "add_mgr")
async def btn_add_mgr(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    lang = callback.from_user.language_code or "ru"
    await state.set_state(Form.waiting_manager_id)
    await callback.message.edit_text(get_text(lang, "add_mgr_prompt"), parse_mode="HTML")
    await callback.answer()

@dp.message(Form.waiting_manager_id, F.text)
async def process_add_manager(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    lang = message.from_user.language_code or "ru"
    try:
        new_id = int(message.text.strip())
        await add_manager(new_id)
        await message.answer(get_text(lang, "mgr_added", id=new_id), parse_mode="HTML")
        await state.clear()
        managers = await get_managers()
        await message.answer(get_text(lang, "team_title"), reply_markup=get_managers_menu(lang, managers), parse_mode="HTML")
    except ValueError:
        await message.answer(get_text(lang, "error_id"))

@dp.callback_query(F.data.startswith("del_mgr:"))
async def btn_del_mgr(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    lang = callback.from_user.language_code or "ru"
    mgr_id = int(callback.data.split(":")[1])
    await remove_manager(mgr_id)
    managers = await get_managers()
    await callback.message.edit_text(get_text(lang, "mgr_removed", id=mgr_id), reply_markup=get_managers_menu(lang, managers), parse_mode="HTML")
    await callback.answer("Удалено / Deleted")

# ==========================================
# КОМАНДЫ LEADS И EXPORT
# ==========================================
@dp.message(Command("leads"))
async def show_leads(message: Message):
    if message.from_user.id != ADMIN_ID: return
    leads = await get_last_leads(10)
    if not leads:
        await message.answer("База заявок пуста / DB is empty.")
        return
    text = "📊 <b>Последние 10 записей / Latest 10 leads:</b>\n\n"
    for i, lead in enumerate(leads, 1):
        text += f"{i}. 👤 <b>{lead['name']}</b>\n📞 {lead['contact']}\n🛠 {lead['service']}\n📅 {lead['appointment_date']}\n🕒 {lead['created_at']}\n\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("export"))
async def export_to_excel(message: Message):
    if message.from_user.id != ADMIN_ID: return
    leads = await get_all_leads_for_excel()
    if not leads:
        await message.answer("В базе нет заявок / No leads in DB.")
        return
    wb = Workbook()
    ws = wb.active
    ws.append(["ID", "Name", "Contact", "Telegram", "Service", "Date", "Time", "Created"])
    for lead in leads: ws.append(list(lead))
    for cell in ws[1]: cell.font = cell.font.copy(bold=True)
    filename = "leads_export.xlsx"
    wb.save(filename)
    await message.answer("📥 Формирую файл / Generating file...")
    document = FSInputFile(filename)
    await message.answer_document(document, caption="📊 Excel")
    os.remove(filename)

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    await init_db()
    await bot.set_my_commands([
        BotCommand(command="start", description="🏠 Меню / Menu"),
        BotCommand(command="leads", description="📊 Заявки / Leads"),
        BotCommand(command="export", description="📥 Excel"),
    ])
    print("Бот успешно запущен! Bot is running!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())