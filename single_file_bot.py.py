import asyncio                           # [1]
from os import getenv                    # [1]

from aiogram import Bot, Dispatcher      # [1]
from aiogram.types import Message        # [1]

dp = Dispatcher()                        # [2]


@dp.message()                            # [3]
async def any_message(                   # [4]
        message: Message,                # [5]
):
    await message.answer("Hello world!") # [6]


async def main():
    token = getenv("BOT_TOKEN")          # [7]
    if not token:                        # [7]
        error = "No token provided"      # [7]
        raise ValueError(error)          # [7]
    bot = Bot(token=token)               # [8]

    print("Starting bot...")
    try:
        await dp.start_polling(bot)      # [9]
    finally:
        print("Bot stopped")


if __name__ == '__main__':
    asyncio.run(main())