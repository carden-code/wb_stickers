import logging
import re
import os
from aiogram import Bot, Dispatcher, types, executor
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext

import pandas as pd
import fitz  # PyMuPDF

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Замените 'YOUR_BOT_TOKEN_HERE' на токен вашего бота
API_TOKEN = '7328211477:AAEkRk9zhKC98UdJG86VeTQ5WeSLDzss8Kk'

# Инициализация бота и диспетчера
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Состояния для FSM
class Form(StatesGroup):
    waiting_for_excel = State()
    waiting_for_pdf = State()

# Обработчик команды /start
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    # Создаем инлайн-кнопки
    keyboard = InlineKeyboardMarkup()
    button1 = InlineKeyboardButton("📝 Умная лента сборки заказов WB FBS", callback_data='process_orders')
    button2 = InlineKeyboardButton("💬 Написать в поддержку", url='https://t.me/jeni_ll')  # Замените ссылкой на поддержку
    keyboard.add(button1)
    keyboard.add(button2)

    greeting = (
        "Привет! Я бот для обработки заказов.\n"
        "Выберите одну из опций ниже:"
    )
    await message.answer(greeting, reply_markup=keyboard)

# Обработчик нажатий на инлайн-кнопки
@dp.callback_query_handler(lambda c: c.data == 'process_orders')
async def process_orders(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    await Form.waiting_for_excel.set()
    await bot.send_message(callback_query.from_user.id, "1️⃣ Пришли мне лист подбора в формате Excel❗️")

# Обработчик получения Excel-файла
@dp.message_handler(state=Form.waiting_for_excel, content_types=types.ContentType.DOCUMENT)
async def handle_excel(message: types.Message, state: FSMContext):
    if message.document.mime_type == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' or \
       message.document.mime_type == 'application/vnd.ms-excel':
        # Сохраняем файл
        excel_file = f"excel_{message.from_user.id}.xlsx"
        await message.document.download(destination_file=excel_file)
        await state.update_data(excel_file=excel_file)
        await Form.waiting_for_pdf.set()
        await message.answer("2️⃣ Пришли мне все стикеры в формате PDF❗️")
    else:
        await message.answer("Пожалуйста, отправьте файл в формате Excel.")

# Обработчик получения PDF-файла
@dp.message_handler(state=Form.waiting_for_pdf, content_types=types.ContentType.DOCUMENT)
async def handle_pdf(message: types.Message, state: FSMContext):
    if message.document.mime_type == 'application/pdf':
        # Сохраняем файл
        pdf_file = f"pdf_{message.from_user.id}.pdf"
        await message.document.download(destination_file=pdf_file)
        await state.update_data(pdf_file=pdf_file)
        await message.answer("Немного подожди, сейчас я сформирую файл.")
        
        # Получаем сохраненные данные
        user_data = await state.get_data()
        excel_path = user_data['excel_file']
        pdf_path = user_data['pdf_file']
        
        # Запускаем обработку файлов
        output_pdf_path = f"modified_{message.from_user.id}.pdf"
        success = await process_files(excel_path, pdf_path, output_pdf_path)
        
        if success:
            # Отправляем полученный PDF обратно пользователю
            await bot.send_document(message.from_user.id, open(output_pdf_path, 'rb'))
            # Удаляем временные файлы
            os.remove(excel_path)
            os.remove(pdf_path)
            os.remove(output_pdf_path)
        else:
            await message.answer("Произошла ошибка при обработке файлов. Пожалуйста, проверьте формат файлов и попробуйте снова.")
        
        # Сбрасываем состояние
        await state.finish()
    else:
        await message.answer("Пожалуйста, отправьте файл в формате PDF.")

# Функция обработки файлов
async def process_files(excel_path, pdf_path, output_pdf_path):
    try:
        # Ваш код для обработки файлов (адаптированный для асинхронного выполнения)
        # Здесь используется тот же код, который был предоставлен ранее, с небольшими изменениями

        # Чтение Excel-файла
        data = pd.read_excel(excel_path, header=1)
        required_columns = ['Номер задания', 'Фото', 'Бренд', 'Наименование', 'Размер', 'Цвет', 'Артикул', 'Стикер']
        data.columns = required_columns
        data['Стикер'] = data['Стикер'].astype(str).str.strip()

        # Создание отображений
        sticker_to_article = data.set_index('Стикер')['Артикул'].to_dict()
        grouped_data = data.groupby('Артикул').agg({
            'Стикер': list,
            'Наименование': 'count'
        }).reset_index()

        # Обработка PDF-файла
        doc = fitz.open(pdf_path)
        sticker_page_map = {}
        number_regex = re.compile(r'\b\d+\b')

        for page_index, page in enumerate(doc):
            text = page.get_text()
            numbers = number_regex.findall(text)
            if len(numbers) >= 2:
                sticker_number = f"{numbers[-2]} {numbers[-1]}"
                sticker_page_map[sticker_number] = page_index
            else:
                continue  # Пропустить страницы без нужных чисел

        # Подготовка упорядочивания страниц
        ordered_page_indices = []
        group_insert_indices = []
        current_index = 0

        for row in grouped_data.itertuples():
            article = row.Артикул
            stickers = row.Стикер
            count = row.Наименование
            group_insert_indices.append((current_index, article, count))
            for sticker in stickers:
                sticker = str(sticker).strip()
                if sticker in sticker_page_map:
                    ordered_page_indices.append(sticker_page_map[sticker])
                    current_index += 1

        if not ordered_page_indices:
            return False

        # Переупорядочивание страниц
        doc.select(ordered_page_indices)

        # Настройка шрифта
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"  # Обновите путь при необходимости
        font_name = "DejaVuSans"
        font = fitz.Font(fontname=font_name, fontfile=font_path)

        # Вставка групповых стикеров
        offset = 0
        for insert_index, article, count in group_insert_indices:
            insert_at = min(insert_index + offset, len(doc))
            new_page = doc.new_page(pno=insert_at, width=doc[0].rect.width, height=doc[0].rect.height)
            text = f"Артикул: {article}\nКоличество: {count}"
            new_page.insert_textbox(
                rect=new_page.rect,
                buffer=text,
                fontsize=12,
                fontname=font_name,
                fontfile=font_path,
                color=(0, 0, 0),
                align=1
            )
            offset += 1

        # Замена "WB" на артикул
        MAX_ARTICLE_LENGTH = 50  # Максимальная длина артикула
        for page_index, page in enumerate(doc):
            text = page.get_text()
            if "Артикул:" in text:
                continue
            numbers = number_regex.findall(text)
            if len(numbers) >= 2:
                sticker_number = f"{numbers[-2]} {numbers[-1]}"
                article = sticker_to_article.get(sticker_number)
                if article:
                    article_text = str(article)
                    if len(article_text) > MAX_ARTICLE_LENGTH:
                        article_text = article_text[:MAX_ARTICLE_LENGTH] + '...'
                    text_instances = page.search_for("WB")
                    if text_instances:
                        for inst in text_instances:
                            page.draw_rect(inst, color=(1, 1, 1), fill=(1, 1, 1))
                            expanded_inst = inst + (-1, -1, 1, 1)
                            page.insert_textbox(
                                rect=expanded_inst,
                                buffer=article_text,
                                fontsize=6,
                                fontname=font_name,
                                fontfile=font_path,
                                color=(0, 0, 0),
                                align=1,
                                rotate=90
                            )
        # Сохранение PDF
        doc.save(output_pdf_path)
        doc.close()
        return True
    except Exception as e:
        logging.error(f"Ошибка при обработке файлов: {e}")
        return False

# Обработчик неизвестных сообщений
@dp.message_handler()
async def unknown_message(message: types.Message):
    await message.answer("Пожалуйста, выберите опцию из меню или отправьте необходимые файлы.")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)