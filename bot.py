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

# 1. Загружаем настройки из .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
MANAGER_USERNAME = os.getenv("MANAGER_USERNAME")
WEBAPP_URL = os.getenv("WEBAPP_URL")

# Константы
DB_NAME = "leads.db"
SPAM_DELAY = 60  # Секунд между заявками

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

last_request_time: dict[int, float] = {}

# 2. СОСТОЯНИЯ (FSM)
class Form(StatesGroup):
    waiting_name = State()
    waiting_phone = State()
    waiting_feedback = State()
    waiting_broadcast = State()
    waiting_manager_id = State()

# ==========================================
# 3. БАЗА ДАННЫХ (SQLite)
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
        except Exception:
            pass 

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, joined_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS managers (
                user_id INTEGER PRIMARY KEY, added_at TEXT
            )
        """)
        await db.commit()

async def save_user(user_id: int, username: str, first_name: str):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, joined_at) VALUES (?, ?, ?, ?)", (user_id, username, first_name, now))
        await db.commit()

async def save_lead(user_id: int, name: str, contact: str, username: str, service: str = "—", app_date: str = "—", app_time: str = "—"):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO leads (user_id, name, contact, username, service, appointment_date, appointment_time, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
            (user_id, name, contact, username, service, app_date, app_time, now)
        )
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
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            users_count = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM leads") as cursor:
            leads_count = (await cursor.fetchone())[0]
        today = datetime.date.today().isoformat()
        async with db.execute("SELECT COUNT(*) FROM leads WHERE date(created_at) = ?", (today,)) as cursor:
            today_leads = (await cursor.fetchone())[0]
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


# ==========================================
# 4. КЛАВИАТУРЫ
# ==========================================
def get_phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить телефон", request_contact=True)], [KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True, one_time_keyboard=True
    )

def get_main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚀 Записаться онлайн", web_app=WebAppInfo(url=WEBAPP_URL))],
            [KeyboardButton(text="🏠 Главное меню")]
        ],
        resize_keyboard=True, is_persistent=True
    )

def get_inline_menu(user_id: int):
    keyboard = [
        [InlineKeyboardButton(text="📋 Прайс-лист", callback_data="price")],
        [InlineKeyboardButton(text="❓ FAQ", callback_data="faq"), InlineKeyboardButton(text="💬 Менеджер", callback_data="contact")],
        [InlineKeyboardButton(text="📝 Оставить отзыв", callback_data="feedback")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_admin_panel():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Управление командой", callback_data="manage_managers")],
        [InlineKeyboardButton(text="📈 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
    ])

def get_managers_menu(managers: list):
    keyboard = [[InlineKeyboardButton(text="➕ Добавить сотрудника", callback_data="add_mgr")]]
    for mgr in managers:
        keyboard.append([InlineKeyboardButton(text=f"❌ Удалить ID: {mgr}", callback_data=f"del_mgr:{mgr}")])
    keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ==========================================
# 5. ОБРАБОТЧИКИ КЛИЕНТСКОЙ ЧАСТИ
# ==========================================
@dp.message(Command("start"))
@dp.message(F.text == "🏠 Главное меню")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await save_user(message.from_user.id, message.from_user.username or "", message.from_user.first_name)
    
    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\n"
        "Добро пожаловать в наш сервис. Вы можете записаться на услугу через удобное приложение или связаться с нами.",
        reply_markup=get_main_menu_keyboard()
    )
    await message.answer("Выберите действие в меню ниже:", reply_markup=get_inline_menu(message.from_user.id))

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text("Выберите действие:", reply_markup=get_inline_menu(callback.from_user.id))
    await callback.answer()

@dp.callback_query(F.data == "price")
async def show_price(callback: CallbackQuery):
    await callback.message.answer("💰 <b>Наши услуги:</b>\n\n• Консультация — 1000 ₽\n• Разработка бота — от 15000 ₽\n• Поддержка — 5000 ₽/мес", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "faq")
async def show_faq(callback: CallbackQuery):
    await callback.message.answer("❓ <b>Часто задаваемые вопросы:</b>\n\n<b>Как записаться?</b>\nНажмите кнопку «Записаться онлайн» внизу экрана.\n\n<b>Как с вами связаться?</b>\nНажмите кнопку «Менеджер».", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "contact")
async def show_contact(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написать менеджеру", url=f"https://t.me/{MANAGER_USERNAME}")]])
    await callback.message.answer("💬 Нажмите на кнопку ниже, чтобы перейти в чат с нашим специалистом.", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(F.data == "feedback")
async def start_feedback(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_feedback)
    await callback.message.answer("📝 Пожалуйста, напишите ваш отзыв или пожелание одним сообщением:")
    await callback.answer()

@dp.message(Form.waiting_feedback, F.text)
async def process_feedback(message: Message, state: FSMContext):
    text = f"📩 <b>Новый отзыв!</b>\n\nОт: @{message.from_user.username or 'нет'}\n\n<i>{message.text}</i>"
    try: 
        await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
    except Exception: 
        pass
    await message.answer("Спасибо за ваш отзыв! Мы обязательно его учтем. 🙌", reply_markup=get_main_menu_keyboard())
    await state.clear()

@dp.message(F.text == "❌ Отмена")
async def cancel_form(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено. Вы вернулись в главное меню.", reply_markup=get_main_menu_keyboard())


# ==========================================
# 6. ОБРАБОТКА WEB APP (MINI APP)
# ==========================================
@dp.message(F.web_app_data)
async def handle_webapp_data(message: Message):
    lang = message.from_user.language_code or "ru"
    try:
        data = json.loads(message.web_app_data.data)
        name = data.get("name")
        contact = data.get("phone")
        bot_type = data.get("bot_type", "Не указан")
        description = data.get("description", "Без описания")
        username = message.from_user.username or "нет"
        
        # Сохраняем в БД
        await save_lead(message.from_user.id, name, contact, username, service=bot_type, app_date="—", app_time="—")
        
        # Формируем красивое уведомление для вас (Админа)
        lead_text = (
            f"🔥 <b>Новая заявка на разработку!</b>\n\n"
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
            try: 
                await bot.send_message(recipient_id, lead_text, parse_mode="HTML")
            except Exception: 
                pass
                
        await message.answer(f"Спасибо, {name}! Ваша заявка на разработку принята. Я свяжусь с вами в ближайшее время. 🙌", reply_markup=get_main_menu_keyboard(lang))
    except Exception as e:
        logging.error(f"Ошибка Web App: {e}")
        await message.answer(get_text(lang, "lead_error"))


# ==========================================
# 7. АДМИН-ПАНЕЛЬ
# ==========================================
@dp.callback_query(F.data == "admin_panel")
async def show_admin_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.edit_text("👑 <b>Админ-панель</b>\nВыберите раздел для управления:", reply_markup=get_admin_panel(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def btn_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    users, leads, today = await get_stats()
    text = f"📊 <b>Статистика:</b>\n\n👥 Пользователей: {users}\n📝 Всего заявок: {leads}\n🔥 Заявок за сегодня: {today}"
    try:
        await callback.message.edit_text(text, reply_markup=get_admin_panel(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

@dp.callback_query(F.data == "admin_broadcast")
async def btn_broadcast(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(Form.waiting_broadcast)
    await callback.message.answer("📢 Отправьте сообщение (текст или фото), которое нужно разослать всем пользователям:")
    await callback.answer()

@dp.message(Form.waiting_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    users = await get_all_user_ids()
    if not users:
        await message.answer("В базе пока нет пользователей.")
        await state.clear()
        return
    success, fail = 0, 0
    status_msg = await message.answer(f"⏳ Рассылка начата для {len(users)} чел...")
    for user_id in users:
        try:
            await message.copy_to(chat_id=user_id)
            success += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await status_msg.edit_text(f"✅ Рассылка завершена!\n\nДоставлено: {success}\nНе доставлено: {fail}")
    await state.clear()


# --- Управление менеджерами ---
@dp.callback_query(F.data == "manage_managers")
async def btn_manage_managers(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    managers = await get_managers()
    text = "👥 <b>Управление командой</b>\n\n"
    text += "Нажмите ❌, чтобы удалить сотрудника из рассылки заявок:" if managers else "В команде пока нет менеджеров."
    await callback.message.edit_text(text, reply_markup=get_managers_menu(managers), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "add_mgr")
async def btn_add_mgr(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(Form.waiting_manager_id)
    await callback.message.edit_text("Отправьте <b>ID</b> пользователя, которого хотите добавить в команду.\n\n<i>(Узнать ID можно через @userinfobot)</i>", parse_mode="HTML")
    await callback.answer()

@dp.message(Form.waiting_manager_id, F.text)
async def process_add_manager(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        new_id = int(message.text.strip())
        await add_manager(new_id)
        await message.answer(f"✅ Сотрудник с ID <code>{new_id}</code> добавлен.", parse_mode="HTML")
        await state.clear()
        managers = await get_managers()
        await message.answer("👥 <b>Управление командой</b>", reply_markup=get_managers_menu(managers), parse_mode="HTML")
    except ValueError:
        await message.answer("❌ Ошибка: ID должен состоять только из цифр.")

@dp.callback_query(F.data.startswith("del_mgr:"))
async def btn_del_mgr(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    mgr_id = int(callback.data.split(":")[1])
    await remove_manager(mgr_id)
    managers = await get_managers()
    await callback.message.edit_text(f"✅ Сотрудник <code>{mgr_id}</code> удален из команды.\n\n👥 <b>Управление командой</b>", reply_markup=get_managers_menu(managers), parse_mode="HTML")
    await callback.answer("Удалено")


# --- Команды админа ---
@dp.message(Command("leads"))
async def show_leads(message: Message):
    if message.from_user.id != ADMIN_ID: return
    leads = await get_last_leads(10)
    if not leads:
        await message.answer("База заявок пуста.")
        return
    text = "📊 <b>Последние 10 записей:</b>\n\n"
    for i, lead in enumerate(leads, 1):
        text += (
            f"{i}. 👤 <b>{lead['name']}</b>\n"
            f"📞 {lead['contact']}\n"
            f"🛠 {lead['service']}\n"
            f"📅 Запись: {lead['appointment_date']} в {lead['appointment_time']}\n"
            f"🕒 Создана: {lead['created_at']}\n\n"
        )
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("export"))
async def export_to_excel(message: Message):
    if message.from_user.id != ADMIN_ID: return
    leads = await get_all_leads_for_excel()
    if not leads:
        await message.answer("В базе нет заявок для выгрузки.")
        return
    wb = Workbook()
    ws = wb.active
    ws.append(["ID", "Имя", "Контакт", "Telegram", "Услуга", "Дата записи", "Время записи", "Время создания"])
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


# ==========================================
# 8. ЗАПУСК БОТА
# ==========================================
async def main():
    await init_db()
    
    # Устанавливаем меню команд в Telegram
    await bot.set_my_commands([
        BotCommand(command="start", description="🏠 Главное меню"),
        BotCommand(command="leads", description="📊 Последние заявки (Админ)"),
        BotCommand(command="export", description="📥 Выгрузить в Excel (Админ)"),
    ])
    
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())