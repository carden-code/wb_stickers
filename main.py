from aiogram import executor
from aiogram.types import BotCommand

from bot_setup import bot, dp
from handlers import admin, start, sticker


async def set_commands():
    commands = [
        BotCommand(command="/start", description="Главное меню"),
    ]
    await bot.set_my_commands(commands)

async def on_startup(_):
    await set_commands()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
