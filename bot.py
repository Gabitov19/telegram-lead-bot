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

load_dotenv()

# === НАСТРОЙКИ ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
SPAM_DELAY = 60
DB_NAME = "leads.db"
MANAGER_USERNAME = "gabitov19" 
WEBAPP_URL = "https://gabitov19.github.io/telegram-lead-bot/webapp/?v=2" # <--- ВАША ССЫЛКА

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

last_request_time: dict[int, float] = {}


# === БАЗА ДАННЫХ ===
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, contact TEXT, username TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS managers (
                user_id INTEGER PRIMARY KEY, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def save_user(user_id: int, username: str, first_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)", (user_id, username, first_name))
        await db.commit()

async def save_lead(user_id: int, name: str, contact: str, username: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO leads (user_id, name, contact, username) VALUES (?, ?, ?, ?)", (user_id, name, contact, username))
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
        today = datetime.date.today().isoformat()
        async with db.execute("SELECT COUNT(*) FROM leads WHERE date(created_at) = ?", (today,)) as cursor:
            today_leads = (await cursor.fetchone())[0]
    return users_count, leads_count, today_leads

async def get_all_user_ids():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            return [row[0] for row in await cursor.fetchall()]

async def add_manager(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO managers (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def remove_manager(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM managers WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_managers() -> list[int]:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM managers") as cursor:
            return [row[0] for row in await cursor.fetchall()]


# === СОСТОЯНИЯ ===
class Form(StatesGroup):
    waiting_name = State()
    waiting_phone = State()
    waiting_feedback = State()
    waiting_broadcast = State()
    waiting_manager_id = State()


# === КЛАВИАТУРЫ ===
def get_phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить телефон", request_contact=True)], [KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True, one_time_keyboard=True
    )

def get_main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚀 Оставить заявку", web_app=WebAppInfo(url=WEBAPP_URL))],
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
        [InlineKeyboardButton(text="👥 Управление менеджерами", callback_data="manage_managers")],
        [InlineKeyboardButton(text="📈 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
    ])

def get_managers_menu(managers: list):
    keyboard = [[InlineKeyboardButton(text="➕ Добавить менеджера", callback_data="add_mgr")]]
    for mgr in managers:
        keyboard.append([InlineKeyboardButton(text=f"❌ Удалить ID: {mgr}", callback_data=f"del_mgr:{mgr}")])
    keyboard.append([InlineKeyboardButton(text="🔙 Назад в админку", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


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
    await message.answer("Выберите действие:", reply_markup=get_inline_menu(message.from_user.id))

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    await callback.message.edit_text("Выберите действие:", reply_markup=get_inline_menu(callback.from_user.id))
    await callback.answer()


# === ОБРАБОТКА ДАННЫХ ИЗ WEB APP ===
@dp.message(F.web_app_data)
async def handle_webapp_data(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
        name = data.get("name")
        contact = data.get("phone")
        service = data.get("service", "Не указана")
        date = data.get("date", "Не указана")
        time = data.get("time", "Не указано")
        username = message.from_user.username or "нет"
        
        # Сохраняем в БД (пока без новых полей, чтобы не усложнять структуру таблицы, 
        # но в будущем можно добавить колонки service, date, time)
        await save_lead(message.from_user.id, name, contact, username)
        
        # Красиво форматируем уведомление для админа
        lead_text = (
            f"🔔 <b>Новая запись!</b>\n\n"
            f"👤 Имя: {name}\n"
            f"📞 Контакт: {contact}\n"
            f"🛠 Услуга: {service}\n"
            f"📅 Дата: {date} в {time}\n"
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
                
        await message.answer(f"Спасибо, {name}! Вы записаны на {date} в {time}. 🙌", reply_markup=get_main_menu_keyboard())
    except Exception as e:
        logging.error(f"Ошибка Web App: {e}")
        await message.answer("Произошла ошибка при обработке данных.")


# === АДМИН-ПАНЕЛЬ ===
@dp.callback_query(F.data == "admin_panel")
async def show_admin_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    await callback.message.edit_text("👑 <b>Админ-панель</b>\nВыберите раздел:", reply_markup=get_admin_panel(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def btn_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    users, leads, today = await get_stats()
    text = f"📊 <b>Статистика бота:</b>\n\n👥 Пользователей: {users}\n📝 Всего заявок: {leads}\n🔥 Заявок за сегодня: {today}"
    try:
        await callback.message.edit_text(text, reply_markup=get_admin_panel(), parse_mode="HTML")
    except Exception:
        pass # Игнорируем ошибку, если текст не изменился
    await callback.answer()

@dp.callback_query(F.data == "admin_broadcast")
async def btn_broadcast(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(Form.waiting_broadcast)
    await callback.message.answer("📢 Отправьте сообщение для рассылки (можно с фото):")
    await callback.answer()

@dp.callback_query(F.data == "manage_managers")
async def btn_manage_managers(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    managers = await get_managers()
    text = "👥 <b>Управление менеджерами</b>\n\n"
    text += "Текущие менеджеры (нажмите ❌, чтобы удалить):" if managers else "В команде пока нет менеджеров."
    await callback.message.edit_text(text, reply_markup=get_managers_menu(managers), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "add_mgr")
async def btn_add_mgr(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(Form.waiting_manager_id)
    await callback.message.edit_text("Отправьте <b>ID</b> пользователя для назначения менеджером.", parse_mode="HTML")
    await callback.answer()

@dp.message(Form.waiting_manager_id, F.text)
async def process_add_manager(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        new_id = int(message.text.strip())
        await add_manager(new_id)
        await message.answer(f"✅ Менеджер <code>{new_id}</code> добавлен.", parse_mode="HTML")
        await state.clear()
        managers = await get_managers()
        await message.answer("👥 <b>Управление менеджерами</b>", reply_markup=get_managers_menu(managers), parse_mode="HTML")
    except ValueError:
        await message.answer("❌ ID должен состоять только из цифр.")

@dp.callback_query(F.data.startswith("del_mgr:"))
async def btn_del_mgr(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    mgr_id = int(callback.data.split(":")[1])
    await remove_manager(mgr_id)
    managers = await get_managers()
    await callback.message.edit_text(f"✅ Менеджер <code>{mgr_id}</code> удален.\n\n👥 <b>Управление менеджерами</b>", reply_markup=get_managers_menu(managers), parse_mode="HTML")
    await callback.answer("Удалено")


# === FAQ И МЕНЕДЖЕР ===
@dp.callback_query(F.data == "faq")
async def show_faq(callback: CallbackQuery):
    await callback.message.answer("❓ <b>FAQ:</b>\n\n<b>Вопрос 1:</b> Как это работает?\n<i>Оставляете заявку, мы связываемся.</i>", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "contact")
async def show_contact(callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Написать менеджеру", url=f"https://t.me/{MANAGER_USERNAME}")]])
    await callback.message.answer("💬 <b>Связь с менеджером</b>", reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


# === ОБРАТНАЯ СВЯЗЬ ===
@dp.callback_query(F.data == "feedback")
async def start_feedback(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Form.waiting_feedback)
    await callback.message.answer("📝 Напишите ваш отзыв:")
    await callback.answer()

@dp.message(Form.waiting_feedback, F.text)
async def process_feedback(message: Message, state: FSMContext):
    text = f"📩 <b>Новый отзыв!</b>\n\nОт: @{message.from_user.username or 'нет'}\n\n<i>{message.text}</i>"
    try: await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
    except Exception: pass
    await message.answer("Спасибо! 🙌", reply_markup=get_main_menu_keyboard())
    await state.clear()


# === ПРАЙС ===
@dp.callback_query(F.data == "price")
async def show_price(callback: CallbackQuery):
    await callback.message.answer("💰 Услуги:\n• Консультация — 1000 ₽\n• Разработка бота — от 15000 ₽")
    await callback.answer()


# === СТАРАЯ ЗАЯВКА (ТЕКСТОМ) ===
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
    await message.answer(f"Приятно познакомиться, {name}! 👋\nНажмите кнопку ниже.", reply_markup=get_phone_keyboard())

@dp.message(Form.waiting_phone, F.contact | F.text)
async def process_phone(message: Message, state: FSMContext):
    contact = message.contact.phone_number if message.contact else message.text.strip()
    if len(contact) < 5:
        await message.answer("Напишите телефон или @username.")
        return
    data = await state.get_data()
    name = data.get("name", "—")
    username = message.from_user.username or "нет"
    
    await save_lead(message.from_user.id, name, contact, username)
    
    lead_text = f"🔔 <b>Новая заявка!</b>\n\n👤 Имя: {name}\n📞 Контакт: {contact}\n💬 Telegram: @{username}"
    managers = await get_managers()
    recipients = set(managers)
    recipients.add(ADMIN_ID)

    for recipient_id in recipients:
        try: await bot.send_message(recipient_id, lead_text, parse_mode="HTML")
        except Exception: pass

    await message.answer(f"Спасибо, {name}! Заявка принята. 🙌", reply_markup=get_main_menu_keyboard())
    await state.clear()

@dp.message(F.text == "❌ Отмена")
async def cancel_form(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=get_main_menu_keyboard())


# === РАССЫЛКА ===
@dp.message(Form.waiting_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    users = await get_all_user_ids()
    if not users:
        await message.answer("Нет пользователей.")
        await state.clear()
        return
    success, fail = 0, 0
    status_msg = await message.answer(f"⏳ Начинаю рассылку для {len(users)} чел...")
    for user_id in users:
        try:
            await message.copy_to(chat_id=user_id)
            success += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await status_msg.edit_text(f"✅ Рассылка завершена!\nДоставлено: {success}\nНе доставлено: {fail}")
    await state.clear()


# === КОМАНДЫ LEADS И EXPORT ===
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
        text += f"{i}. 👤 <b>{lead['name']}</b>\n📞 {lead['contact']}\n🕒 {date_str}\n\n"
    await message.answer(text, parse_mode="HTML")

@dp.message(Command("export"))
async def export_to_excel(message: Message):
    if message.from_user.id != ADMIN_ID: return
    leads = await get_all_leads_for_excel()
    if not leads:
        await message.answer("В базе нет заявок.")
        return
    wb = Workbook()
    ws = wb.active
    ws.append(["ID", "Имя", "Контакт", "Telegram", "Дата"])
    for lead in leads: ws.append(list(lead))
    for cell in ws[1]: cell.font = cell.font.copy(bold=True)
    filename = "leads_export.xlsx"
    wb.save(filename)
    await message.answer("📥 Формирую файл...")
    document = FSInputFile(filename)
    await message.answer_document(document, caption="📊 Все заявки в Excel!")
    os.remove(filename)


# === ЗАПУСК ===
async def main():
    await init_db()
    await bot.set_my_commands([
        BotCommand(command="start", description="🏠 Главное меню"),
        BotCommand(command="leads", description="📊 Заявки (Админ)"),
        BotCommand(command="export", description="📥 Excel (Админ)"),
    ])
    print("Бот запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())