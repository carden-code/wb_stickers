import asyncio
import secrets

from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Command
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from bot_setup import bot, dp, logger
from config import ADMINS
from database.setup import AccessKey, User, get_session


# Состояние для написания поста
class PostWritingState(StatesGroup):
    content = State()


# Команда для администратора
@dp.message_handler(Command('admin'), state='*')
async def admin_command(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        return

    # Получение количества уникальных пользователей
    async with get_session() as session:
        user_count_result = await session.execute(select(User))
        user_count = len(user_count_result.scalars().all())

    keyboard = InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton(text="Написать пост всем", callback_data="write_post_to_all"),
        #generate_link
        InlineKeyboardButton(text="Сгенерировать ссылку", callback_data="generate_link"),

    )

    await message.answer(f"Количество уникальных пользователей: {user_count}", reply_markup=keyboard)

@dp.callback_query_handler(text="admin", state="*")
async def admin_menu(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()

    # Получение количества уникальных пользователей
    async with get_session() as session:
        user_count_result = await session.execute(select(User))
        user_count = len(user_count_result.scalars().all())

    keyboard = InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton(text="Написать пост всем", callback_data="write_post_to_all"),
        InlineKeyboardButton(text="Сгенерировать ссылку", callback_data="generate_link"),
    )

    await callback_query.message.answer(f"Количество уникальных пользователей: {user_count}", reply_markup=keyboard)
    await callback_query.answer()

# Обработчик кнопки для написания поста
@dp.callback_query_handler(text="write_post_to_all")
async def write_post_to_all(callback_query: types.CallbackQuery):
    await PostWritingState.content.set()
    cancel_keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton(text="Отмена", callback_data="admin"))

    await callback_query.message.answer("Отправьте сообщение, изображение или видео для рассылки:", reply_markup=cancel_keyboard)
    await callback_query.answer()

@dp.message_handler(content_types=['text', 'photo', 'video'], state=PostWritingState.content)
async def admin_post_send(message: types.Message, state: FSMContext):
    successful_count, failed_count = 0, 0
    async with get_session() as session:
        result = await session.execute(select(User))
        users_list = result.scalars().all()

        if not users_list:
            await message.answer("В базе данных нет пользователей.")
        else:
            for user in users_list:
                try:
                    if message.content_type == 'text':
                        await bot.send_message(user.telegram_id, message.text)
                    else:
                        caption = message.caption if message.caption and len(message.caption) <= 1024 else None

                        if message.content_type == 'photo':
                            await bot.send_photo(user.telegram_id, message.photo[-1].file_id, caption=caption)
                        elif message.content_type == 'video':
                            await bot.send_video(user.telegram_id, message.video.file_id, caption=caption)

                        # Если подпись слишком длинная, отправляем её как отдельное текстовое сообщение
                        if message.caption and len(message.caption) > 1024:
                            await asyncio.sleep(1)  # Задержка для избежания ограничений Telegram на частоту сообщений
                            await bot.send_message(user.telegram_id, message.caption)

                    successful_count += 1
                    await asyncio.sleep(1)  # Задержка перед отправкой следующего сообщения
                except Exception as e:
                    failed_count += 1
                    logger.error(f"Ошибка при отправке сообщения пользователю {user.telegram_id}: {e}")

        await state.finish()
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton(text="Админ меню", callback_data="admin"))
        result_message = f"Сообщение отправлено: {successful_count} пользователям.\nНе удалось отправить: {failed_count} пользователям.\n\nАдмин меню: /admin"
        await message.answer(result_message, reply_markup=keyboard)


@dp.callback_query_handler(text="generate_link")
async def generate_link(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMINS:
        await callback_query.message.answer("У вас нет прав для выполнения этой команды.")
        return

    # Генерируем уникальный ключ
    unique_key = secrets.token_urlsafe(16)  # Генерируем безопасный случайный ключ

    # Сохраняем его в базе данных
    async with get_session() as session:
        access_key = AccessKey(key=unique_key)
        session.add(access_key)
        await session.commit()

    # Формируем одноразовую ссылку
    bot_username = (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={unique_key}"

    # Отправляем ссылку в чат
    await callback_query.message.answer(f"Одноразовая ссылка для доступа: {link}")
    
    # Закрываем всплывающее уведомление
    await callback_query.answer()



