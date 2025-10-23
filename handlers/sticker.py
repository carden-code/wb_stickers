import os
from aiogram import types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot_setup import bot, dp
from utils.create_pdf import process_files
from utils.create_ozon_pdf import process_ozon_files


TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024  # 20 MB bot download limit


class Form(StatesGroup):
    waiting_for_wb_excel = State()
    waiting_for_wb_pdf = State()
    waiting_for_ozon_assembly = State()
    waiting_for_ozon_ticket = State()


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup().add(InlineKeyboardButton(text="Отмена", callback_data="menu"))


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup().add(InlineKeyboardButton(text="Главное меню", callback_data="menu"))


def _safe_remove(path: str | None) -> None:
    if path and os.path.exists(path):
        os.remove(path)


async def _clear_previous_keyboard(state: FSMContext, user_id: int) -> None:
    data = await state.get_data()
    msg_id = data.get("message_id")
    if msg_id:
        try:
            await bot.edit_message_reply_markup(user_id, msg_id, reply_markup=None)
        except Exception:
            pass


@dp.callback_query_handler(lambda c: c.data == 'process_orders_wb', state='*')
async def process_orders_wb(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await Form.waiting_for_wb_excel.set()
    keyboard = _cancel_keyboard()
    try:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    msg = await callback_query.message.answer(
        "1️⃣ Пришли мне лист подбора в формате Excel❗️",
        reply_markup=keyboard,
    )
    await state.update_data(message_id=msg.message_id)
    await bot.answer_callback_query(callback_query.id)


@dp.callback_query_handler(lambda c: c.data == 'process_orders_ozon', state='*')
async def process_orders_ozon(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await Form.waiting_for_ozon_assembly.set()
    keyboard = _cancel_keyboard()
    try:
        await callback_query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    msg = await callback_query.message.answer(
        "1️⃣ Пришли PDF сборочного листа (assembly)❗️",
        reply_markup=keyboard,
    )
    await state.update_data(message_id=msg.message_id)
    await bot.answer_callback_query(callback_query.id)


@dp.message_handler(state=Form.waiting_for_wb_excel, content_types=types.ContentType.DOCUMENT)
async def handle_wb_excel(message: types.Message, state: FSMContext):
    await _clear_previous_keyboard(state, message.from_user.id)

    mime = message.document.mime_type
    if mime in {
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.ms-excel',
    }:
        if message.document.file_size and message.document.file_size > TELEGRAM_DOWNLOAD_LIMIT:
            msg = await message.answer(
                "Файл превышает 20 МБ и не может быть получен ботом.",
                reply_markup=_cancel_keyboard(),
            )
            await state.update_data(message_id=msg.message_id)
            return
        excel_file = f"excel_{message.from_user.id}.xlsx"
        await message.document.download(destination_file=excel_file)
        await state.update_data(excel_file=excel_file)
        await Form.waiting_for_wb_pdf.set()
        msg = await message.answer("2️⃣ Пришли мне все стикеры в формате PDF❗️", reply_markup=_cancel_keyboard())
    else:
        msg = await message.answer("Пожалуйста, отправьте файл в формате Excel.", reply_markup=_cancel_keyboard())

    await state.update_data(message_id=msg.message_id)


@dp.message_handler(state=Form.waiting_for_wb_pdf, content_types=types.ContentType.DOCUMENT)
async def handle_wb_pdf(message: types.Message, state: FSMContext):
    await _clear_previous_keyboard(state, message.from_user.id)

    if message.document.mime_type != 'application/pdf':
        msg = await message.answer("Пожалуйста, отправьте файл в формате PDF.", reply_markup=_cancel_keyboard())
        await state.update_data(message_id=msg.message_id)
        return

    if message.document.file_size and message.document.file_size > TELEGRAM_DOWNLOAD_LIMIT:
        msg = await message.answer(
            "Файл превышает 20 МБ и не может быть получен ботом.",
            reply_markup=_cancel_keyboard(),
        )
        await state.update_data(message_id=msg.message_id)
        return

    pdf_file = f"pdf_{message.from_user.id}.pdf"
    await message.document.download(destination_file=pdf_file)
    await state.update_data(pdf_file=pdf_file)
    await message.answer("Немного подожди, сейчас я сформирую файл.")

    user_data = await state.get_data()
    excel_path = user_data.get('excel_file')
    pdf_path = user_data.get('pdf_file')
    output_pdf_path = f"modified_{message.from_user.id}.pdf"

    success = await process_files(excel_path, pdf_path, output_pdf_path)
    keyboard = _menu_keyboard()

    if success and os.path.exists(output_pdf_path):
        with open(output_pdf_path, 'rb') as file:
            await bot.send_document(message.from_user.id, file)
        await message.answer("✅ Обработка завершена.", reply_markup=keyboard)
    else:
        await message.answer(
            "Произошла ошибка при обработке файлов. Пожалуйста, проверьте формат файлов и попробуйте снова.",
            reply_markup=keyboard,
        )

    _safe_remove(excel_path)
    _safe_remove(pdf_path)
    _safe_remove(output_pdf_path)
    await state.finish()


@dp.message_handler(state=Form.waiting_for_ozon_assembly, content_types=types.ContentType.DOCUMENT)
async def handle_ozon_assembly(message: types.Message, state: FSMContext):
    await _clear_previous_keyboard(state, message.from_user.id)

    if message.document.mime_type != 'application/pdf':
        msg = await message.answer("Пожалуйста, отправьте сборочный лист в формате PDF.", reply_markup=_cancel_keyboard())
        await state.update_data(message_id=msg.message_id)
        return

    if message.document.file_size and message.document.file_size > TELEGRAM_DOWNLOAD_LIMIT:
        msg = await message.answer(
            "Файл превышает 20 МБ и не может быть получен ботом.",
            reply_markup=_cancel_keyboard(),
        )
        await state.update_data(message_id=msg.message_id)
        return

    assembly_file = f"ozon_assembly_{message.from_user.id}.pdf"
    await message.document.download(destination_file=assembly_file)
    await state.update_data(assembly_file=assembly_file)
    await Form.waiting_for_ozon_ticket.set()
    msg = await message.answer("2️⃣ Пришли PDF со стикерами (ticket)❗️", reply_markup=_cancel_keyboard())
    await state.update_data(message_id=msg.message_id)


@dp.message_handler(state=Form.waiting_for_ozon_ticket, content_types=types.ContentType.DOCUMENT)
async def handle_ozon_ticket(message: types.Message, state: FSMContext):
    await _clear_previous_keyboard(state, message.from_user.id)

    if message.document.mime_type != 'application/pdf':
        msg = await message.answer("Пожалуйста, отправьте файл ticket в формате PDF.", reply_markup=_cancel_keyboard())
        await state.update_data(message_id=msg.message_id)
        return

    if message.document.file_size and message.document.file_size > TELEGRAM_DOWNLOAD_LIMIT:
        msg = await message.answer(
            "Файл превышает 20 МБ и не может быть получен ботом.",
            reply_markup=_cancel_keyboard(),
        )
        await state.update_data(message_id=msg.message_id)
        return

    ticket_file = f"ozon_ticket_{message.from_user.id}.pdf"
    await message.document.download(destination_file=ticket_file)
    await state.update_data(ticket_file=ticket_file)
    await message.answer("Немного подожди, сейчас я сформирую файл.")

    user_data = await state.get_data()
    assembly_path = user_data.get('assembly_file')
    ticket_path = user_data.get('ticket_file')
    output_pdf_path = f"ozon_sorted_{message.from_user.id}.pdf"

    success = await process_ozon_files(assembly_path, ticket_path, output_pdf_path)
    keyboard = _menu_keyboard()

    if success and os.path.exists(output_pdf_path):
        with open(output_pdf_path, 'rb') as file:
            await bot.send_document(message.from_user.id, file)
        await message.answer("✅ Обработка завершена.", reply_markup=keyboard)
    else:
        await message.answer(
            "Не удалось обработать файлы OZON. Проверь, что отправил сборочный лист и стикеры в формате PDF и попробуй снова.",
            reply_markup=keyboard,
        )

    _safe_remove(assembly_path)
    _safe_remove(ticket_path)
    _safe_remove(output_pdf_path)
    await state.finish()
