import os
import logging
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from sqlalchemy import select
from aiogram.dispatcher import FSMContext

from bot_setup import dp, bot
from config import BASE_DIR, IMAGE_NAME
from database.image import get_image_file_id, save_image_file_id
from database.setup import AccessKey, User, get_session
from texts.start import START_TEXT

# Настройка логгера
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Функция для предоставления доступа к боту
async def grant_access_to_bot(message: types.Message, user_id: int):
    user_name = message.from_user.username if message.from_user.username else "Отсутствует"

    async with get_session() as session:
        result = await session.execute(select(User).filter(User.telegram_id == user_id))
        user = result.scalars().first()

        if not user:
            user = User(telegram_id=user_id, username=user_name)
            session.add(user)
            await session.commit()
            logger.info(f"New user created: {user_id}, username: {user_name}")

        keyboard_start = InlineKeyboardMarkup(row_width=1)
        keyboard_start.add(InlineKeyboardButton("📝 Умная лента сборки заказов WB FBS", callback_data='process_orders'))
        keyboard_start.add(InlineKeyboardButton("💬 Написать в поддержку", url='https://t.me/jeni_ll'))
    
        file_id = await get_image_file_id(IMAGE_NAME)
        if file_id:
            await message.answer_photo(photo=file_id, caption=START_TEXT, reply_markup=keyboard_start, parse_mode='HTML')
        else:
            logo_path = os.path.join(BASE_DIR, IMAGE_NAME)
            with open(logo_path, 'rb') as logo:
                msg = await message.answer_photo(logo, caption=START_TEXT, reply_markup=keyboard_start, parse_mode='HTML')
                await save_image_file_id(IMAGE_NAME, msg.photo[-1].file_id)

# Обработчик команды /start
@dp.message_handler(commands=['start'], state="*")
async def start(message: types.Message, state: FSMContext):
    await state.finish()

    user_id = message.from_user.id

    start_param = message.get_args()

    async with get_session() as session:
        # Проверяем, есть ли параметр, т.е. пользователь перешел по админской ссылке
        if start_param:
            result = await session.execute(select(AccessKey).filter(AccessKey.key == start_param, AccessKey.used == False))
            access_key = result.scalars().first()

            if access_key:
                access_key.used = True
                await session.commit()

                # Создаем нового пользователя после успешной проверки ключа
                user_name = message.from_user.username if message.from_user.username else "Отсутствует"
                user = User(telegram_id=user_id, username=user_name)
                session.add(user)
                await session.commit()
                logger.info(f"New user created: {user_id}, username: {user_name}")
            else:
                await message.answer("Доступ ограничен. Обратитесь к администратору.")
                return
        else:
            # Проверяем, существует ли уже пользователь в базе данных
            result = await session.execute(select(User).filter(User.telegram_id == user_id))
            user = result.scalars().first()

            if not user:
                await message.answer("Доступ ограничен. Обратитесь к администратору.")
                return

    # Предоставление доступа к боту
    await grant_access_to_bot(message, user_id)


@dp.callback_query_handler(lambda c: c.data == 'menu', state="*")
async def start(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()

    user_id = callback_query.from_user.id

    async with get_session() as session:
        # Проверяем, существует ли уже пользователь
        result = await session.execute(
            select(User).filter(User.telegram_id == user_id)
        )
        user = result.scalars().first()

        if not user:
            user_name = callback_query.from_user.username if callback_query.from_user.username else "Отсутствует"
            user = User(telegram_id=user_id, username=user_name)
            session.add(user)
            await session.commit()

        keyboard_start = InlineKeyboardMarkup(row_width=1)
        keyboard_start.add(InlineKeyboardButton("📝 Умная лента сборки заказов WB FBS", callback_data='process_orders'))
        keyboard_start.add(InlineKeyboardButton("💬 Написать в поддержку", url='https://t.me/jeni_ll'))

        file_id = await get_image_file_id(IMAGE_NAME)
        if file_id:
            # Проверяем, есть ли у сообщения медиа
            if callback_query.message.photo:
                await callback_query.message.edit_media(
                    InputMediaPhoto(media=file_id, caption=START_TEXT, parse_mode='HTML'),
                    reply_markup=keyboard_start
                )
            else:
                await callback_query.message.edit_reply_markup(reply_markup=None)
                await callback_query.message.answer_photo(
                    photo=file_id,
                    caption=START_TEXT,
                    reply_markup=keyboard_start,
                    parse_mode='HTML'
                )
        else:
            logo_path = os.path.join(BASE_DIR, IMAGE_NAME)
            with open(logo_path, 'rb') as logo:
                if callback_query.message.photo:
                    media = InputMediaPhoto(media=logo, caption=START_TEXT, parse_mode='HTML')
                    await callback_query.message.edit_media(media=media, reply_markup=keyboard_start)
                else:
                    await callback_query.message.edit_text(
                        text=START_TEXT,
                        reply_markup=keyboard_start,
                        parse_mode='HTML'
                    )

        await callback_query.answer()

# Удалять все текстовые сообщения от пользователя
@dp.message_handler(state="*", content_types=types.ContentTypes.TEXT)
async def delete_message(message: types.Message, state: FSMContext):
    #если это не команда /start и не /admin 
    if message.text != '/start' and message.text != '/admin':
        await message.delete()
