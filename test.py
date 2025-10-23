import os
import time
import pandas as pd
import fitz  # PyMuPDF
import re
import logging
import argparse
from typing import Tuple, Dict

import psutil

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# Константы
FONT_NAME = "DejaVuSans"
MAX_ARTICLE_LENGTH = 50  # Максимальная длина названия артикула

def measure_time(func):
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        print(f"{func.__name__} заняло {end_time - start_time:.2f} секунд")
        return result
    return wrapper

def measure_memory():
    process = psutil.Process(os.getpid())
    memory_info = process.memory_info()
    print(f"Использование памяти: {memory_info.rss / 1024 / 1024:.2f} МБ")

@measure_time
def read_excel_data(excel_path: str) -> pd.DataFrame:
    """
    Читает данные из Excel-файла и возвращает DataFrame.

    Параметры:
        excel_path (str): Путь к Excel-файлу.

    Возвращает:
        DataFrame: Обработанные данные с стандартизированными названиями столбцов.
    """
    try:
        data = pd.read_excel(excel_path, header=1)
    except FileNotFoundError:
        logging.error(f"Excel-файл '{excel_path}' не найден.")
        return None
    except Exception as e:
        logging.error(f"Ошибка при чтении Excel-файла: {e}")
        return None

    # Проверка наличия необходимых столбцов
    required_columns = ['Номер задания', 'Фото', 'Бренд', 'Наименование', 'Размер', 'Цвет', 'Артикул', 'Стикер']
    if len(data.columns) < len(required_columns):
        logging.error("Excel-файл имеет недостаточное количество столбцов.")
        return None
    data.columns = required_columns

    if not all(column in data.columns for column in ['Стикер', 'Артикул']):
        logging.error("Excel-файл должен содержать столбцы 'Стикер' и 'Артикул'.")
        return None

    data['Стикер'] = data['Стикер'].astype(str).str.strip()
    return data

@measure_time
def create_mappings(data: pd.DataFrame) -> Tuple[Dict[str, str], pd.DataFrame]:
    """
    Создает отображения номеров стикеров на артикулы и группирует данные по артикулам.

    Параметры:
        data (DataFrame): Входные данные.

    Возвращает:
        Tuple[Dict[str, str], DataFrame]: Кортеж, содержащий отображение "стикер-артикул" и сгруппированные данные.
    """
    sticker_to_article = data.set_index('Стикер')['Артикул'].to_dict()
    grouped_data = data.groupby('Артикул').agg({
        'Стикер': list,
        'Наименование': 'count'
    }).reset_index()
    return sticker_to_article, grouped_data

@measure_time
def process_pdf(pdf_path: str) -> Tuple[fitz.Document, Dict[str, int]]:
    """
    Обрабатывает PDF-файл для извлечения номеров стикеров и сопоставления их с номерами страниц.

    Параметры:
        pdf_path (str): Путь к PDF-файлу.

    Возвращает:
        Tuple[fitz.Document, Dict[str, int]]: Документ PDF и отображение "стикер-страница".
    """
    try:
        doc = fitz.open(pdf_path)
    except FileNotFoundError:
        logging.error(f"PDF-файл '{pdf_path}' не найден.")
        return None, None
    except Exception as e:
        logging.error(f"Ошибка при открытии PDF-файла: {e}")
        return None, None

    sticker_page_map = {}
    number_regex = re.compile(r'\b\d+\b')

    for page_index, page in enumerate(doc):
        text = page.get_text()
        numbers = number_regex.findall(text)
        if len(numbers) >= 2:
            sticker_number = f"{numbers[-2]} {numbers[-1]}"
            sticker_page_map[sticker_number] = page_index
            logging.debug(f"Страница {page_index + 1}: Извлечен номер стикера: {sticker_number}")
        else:
            logging.warning(f"На странице {page_index + 1} найдено менее двух чисел.")

    return doc, sticker_page_map

@measure_time
def prepare_page_ordering(grouped_data: pd.DataFrame, sticker_page_map: Dict[str, int]) -> Tuple[list, list]:
    """
    Подготавливает упорядочивание страниц на основе сгруппированных данных и отображения стикеров.

    Параметры:
        grouped_data (DataFrame): Сгруппированные данные по артикулам.
        sticker_page_map (Dict[str, int]): Отображение номеров стикеров на номера страниц.

    Возвращает:
        Tuple[list, list]: Упорядоченные индексы страниц и позиции для вставки групповых стикеров.
    """
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
            else:
                logging.warning(f"Стикер {sticker} не найден в PDF.")

    return ordered_page_indices, group_insert_indices

@measure_time
def insert_group_stickers(doc: fitz.Document, group_insert_indices: list, font_path: str, font_name: str = FONT_NAME):
    """
    Вставляет групповые стикеры в PDF-документ на указанные позиции.

    Параметры:
        doc (fitz.Document): PDF-документ.
        group_insert_indices (list): Позиции для вставки групповых стикеров.
        font_path (str): Путь к файлу шрифта.
        font_name (str): Название шрифта.
    """
    try:
        font = fitz.Font(fontname=font_name, fontfile=font_path)
    except Exception as e:
        logging.error(f"Ошибка при загрузке шрифта: {e}")
        return

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
        logging.info(f"Вставлен групповой стикер для артикула {article} на позицию {insert_at + 1}")
        offset += 1

@measure_time
def replace_wb_with_article(doc: fitz.Document, sticker_to_article: Dict[str, str], font_path: str, font_name: str = FONT_NAME):
    """
    Заменяет "WB" на соответствующий артикул на каждом стикере.

    Параметры:
        doc (fitz.Document): PDF-документ.
        sticker_to_article (Dict[str, str]): Отображение номеров стикеров на артикулы.
        font_path (str): Путь к файлу шрифта.
        font_name (str): Название шрифта.
    """
    try:
        font = fitz.Font(fontname=font_name, fontfile=font_path)
    except Exception as e:
        logging.error(f"Ошибка при загрузке шрифта: {e}")
        return

    number_regex = re.compile(r'\b\d+\b')

    for page_index, page in enumerate(doc):
        text = page.get_text()
        if "Артикул:" in text:
            continue  # Пропустить групповые стикеры
        numbers = number_regex.findall(text)
        if len(numbers) >= 2:
            sticker_number = f"{numbers[-2]} {numbers[-1]}"
            article = sticker_to_article.get(sticker_number)
            if article:
                # Обрезка артикула, если он слишком длинный
                article_text = str(article)
                if len(article_text) > MAX_ARTICLE_LENGTH:
                    article_text = article_text[:MAX_ARTICLE_LENGTH] + '...'

                text_instances = page.search_for("WB")
                if text_instances:
                    for inst in text_instances:
                        # Удаление существующего текста "WB"
                        page.draw_rect(inst, color=(1, 1, 1), fill=(1, 1, 1))
                        # Немного расширяем область inst
                        expanded_inst = inst + (-1, -1, 1, 1)
                        # Вставка названия артикула с поворотом на 90 градусов
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
                else:
                    logging.warning(f"'WB' не найден на странице {page_index + 1} для стикера {sticker_number}.")
            else:
                logging.warning(f"Номер стикера {sticker_number} не найден в отображении.")
        else:
            logging.warning(f"Не удалось извлечь номер стикера на странице {page_index + 1}.")

@measure_time
def main():
    # Парсинг аргументов командной строки
    parser = argparse.ArgumentParser(description="Обработка PDF стикеров.")
    parser.add_argument("--excel", required=True, help="Путь к Excel-файлу.")
    parser.add_argument("--pdf", required=True, help="Путь к PDF-файлу.")
    parser.add_argument("--font", default="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", help="Путь к файлу шрифта.")
    parser.add_argument("--output", default="modified_output.pdf", help="Имя выходного PDF-файла.")
    args = parser.parse_args()

    print("Начало выполнения скрипта")
    measure_memory()

    # Чтение и проверка данных из Excel
    data = read_excel_data(args.excel)
    if data is None:
        return
    measure_memory()

    sticker_to_article, grouped_data = create_mappings(data)
    measure_memory()

    # Обработка PDF и извлечение номеров стикеров
    doc, sticker_page_map = process_pdf(args.pdf)
    if doc is None or sticker_page_map is None:
        return
    measure_memory()

    # Подготовка упорядочивания страниц на основе данных из Excel
    ordered_page_indices, group_insert_indices = prepare_page_ordering(grouped_data, sticker_page_map)
    if not ordered_page_indices:
        logging.error("Нет страниц для обработки. Выход из программы.")
        return
    measure_memory()

    # Переупорядочивание страниц в PDF
    doc.select(ordered_page_indices)

    # Вставка групповых стикеров в PDF
    insert_group_stickers(doc, group_insert_indices, args.font)
    measure_memory()

    # Замена "WB" на соответствующие артикулы
    replace_wb_with_article(doc, sticker_to_article, args.font)
    measure_memory()

    # Сохранение измененного PDF
    try:
        doc.save(args.output)
        logging.info(f"Измененный PDF сохранен как {args.output}")
    except Exception as e:
        logging.error(f"Ошибка при сохранении PDF-файла: {e}")
    finally:
        doc.close()

    print("Завершение выполнения скрипта")
    measure_memory()
if __name__ == "__main__":
    main()
