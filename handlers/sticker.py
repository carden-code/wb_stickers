import os
from aiogram import types
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot_setup import bot, dp
from utils.create_pdf import process_files

# Состояния для FSM
class Form(StatesGroup):
    waiting_for_excel = State()
    waiting_for_pdf = State()

@dp.callback_query_handler(lambda c: c.data == 'process_orders')
async def process_orders(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.answer_callback_query(callback_query.id)
    await Form.waiting_for_excel.set()
    keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton(text="Отмена", callback_data="menu"))
    msg = await callback_query.message.edit_caption("1️⃣ Пришли мне лист подбора в формате Excel❗️", reply_markup=keyboard)
    await state.update_data(message_id=msg.message_id)
    await callback_query.answer()

@dp.message_handler(state=Form.waiting_for_excel, content_types=types.ContentType.DOCUMENT)
async def handle_excel(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        msg_id = data.get('message_id')
        await bot.edit_message_reply_markup(message.from_user.id, msg_id, reply_markup=None)

    keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton(text="Отмена", callback_data="menu"))

    if message.document.mime_type == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' or \
       message.document.mime_type == 'application/vnd.ms-excel':
        # Сохраняем файл
        excel_file = f"excel_{message.from_user.id}.xlsx"
        await message.document.download(destination_file=excel_file)
        await state.update_data(excel_file=excel_file)
        await Form.waiting_for_pdf.set()

        msg = await message.answer("2️⃣ Пришли мне все стикеры в формате PDF❗️", reply_markup=keyboard)
    else:
        msg = await message.answer("Пожалуйста, отправьте файл в формате Excel.", reply_markup=keyboard)

    await state.update_data(message_id=msg.message_id)

@dp.message_handler(state=Form.waiting_for_pdf, content_types=types.ContentType.DOCUMENT)
async def handle_pdf(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        msg_id = data.get('message_id')
        await bot.edit_message_reply_markup(message.from_user.id, msg_id, reply_markup=None)

    if message.document.mime_type == 'application/pdf':
        # Сохраняем файл
        pdf_file = f"pdf_{message.from_user.id}.pdf"
        await message.document.download(destination_file=pdf_file)
        await state.update_data(pdf_file=pdf_file)
        await message.answer("Немного подожди, сейчас я сформирую файл.")

        user_data = await state.get_data()
        excel_path = user_data['excel_file']
        pdf_path = user_data['pdf_file']

        # Запускаем обработку файлов
        output_pdf_path = f"modified_{message.from_user.id}.pdf"
        success = await process_files(excel_path, pdf_path, output_pdf_path)
        keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton(text="Главное меню", callback_data="menu"))

        if success:
            # Отправляем полученный PDF обратно пользователю
            with open(output_pdf_path, 'rb') as file:
                await bot.send_document(message.from_user.id, file)

            await message.answer("✅ Обработка завершена.", reply_markup=keyboard)
            # Удаляем временные файлы
            os.remove(excel_path)
            os.remove(pdf_path)
            os.remove(output_pdf_path)
        else:
            keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton(text="Главное меню", callback_data="menu"))
            await message.answer("Произошла ошибка при обработке файлов. Пожалуйста, проверьте формат файлов и попробуйте снова.", reply_markup=keyboard)
            os.remove(excel_path)
            os.remove(pdf_path)

        # Сбрасываем состояние
        await state.finish()
    else:
        keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton(text="Отмена", callback_data="menu"))
        msg = await message.answer("Пожалуйста, отправьте файл в формате PDF.", reply_markup=keyboard)
        await state.update_data(message_id=msg.message_id)

