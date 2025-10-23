# Функция обработки файлов
import logging
import re

import pandas as pd
from config import MAX_ARTICLE_LENGTH
import fitz  # PyMuPDFи

async def process_files(excel_path, pdf_path, output_pdf_path):
    try:
        # Чтение Excel-файла
        data = pd.read_excel(excel_path, header=1)

        # Оставляем только первые 8 столбцов
        data = data.iloc[:, :8]

        # Дальше как было
        required_columns = ['Номер задания', 'Фото', 'Бренд', 'Наименование',
                            'Размер', 'Цвет', 'Артикул', 'Стикер']
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