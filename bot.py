import asyncio
import logging
import time
import os                          # <--- ДОБАВИТЬ
from dotenv import load_dotenv     # <--- ДОБАВИТЬ

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
load_dotenv()                      # <--- ДОБАВИТЬ

# === НАСТРОЙКИ ===
BOT_TOKEN = os.getenv("BOT_TOKEN") # <--- ИЗМЕНИТЬ
ADMIN_ID = int(os.getenv("ADMIN_ID")) # <--- ИЗМЕНИТЬ (обязательно int!)
SPAM_DELAY = 60

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Хранилище для антиспама
last_request_time: dict[int, float] = {}


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
        keyboard=[
            [KeyboardButton(text="🏠 Главное меню")]
        ],
        resize_keyboard=True,
        is_persistent=True  # Эта настройка не дает кнопке скрыться
    )


@dp.message(Command("start"))
@dp.message(F.text == "🏠 Главное меню")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    
    # Кнопки под сообщением
    inline_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Прайс-лист", callback_data="price")],
        [InlineKeyboardButton(text="✍️ Оставить заявку", callback_data="request")],
    ])
    
    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\n"
        "Я — демо-бот.",
        reply_markup=get_main_menu_keyboard()
    )
    
    await message.answer(
        "Выберите действие:",
        reply_markup=inline_keyboard
    )

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


# === ШАГ 2: ТЕЛЕФОН (КНОПКА ИЛИ ТЕКСТ) ===
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

    lead_text = (
        "🔔 <b>Новая заявка!</b>\n\n"
        f"👤 Имя: {name}\n"
        f"📞 Контакт: {contact}\n"
        f"💬 Telegram: @{username} (id: {message.from_user.id})"
    )

    try:
        await bot.send_message(ADMIN_ID, lead_text, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при отправке админу: {e}")
        logging.error(f"Не удалось отправить заявку: {e}")

    await message.answer(
        f"Спасибо, {name}! Заявка принята. 🙌\n"
        "Я свяжусь с вами в ближайшее время.",
        reply_markup=get_main_menu_keyboard()
    )
    await state.clear()


# === ЗАГЛУШКА ДЛЯ НЕИЗВЕСТНЫХ ТЕКСТОВ ===
@dp.message(F.text)
async def fallback(message: Message):
    await message.answer(
        "Я вас не понял. Нажмите кнопку ниже или введите /start.", 
        reply_markup=get_main_menu_keyboard()
    )


# === ЗАПУСК ===
async def main():
    print("Бот запущен. Нажмите Ctrl+C для остановки.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
