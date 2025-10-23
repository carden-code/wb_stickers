#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ozon stickers → WB-style сортировка (v9)
- Вход: assembly_list.pdf (таблица со столбцами: № | Номер отправления | Фото | Товар | Артикул | Кол-во | Этикетка)
        ticket.pdf (лист стикеров: на каждую отгрузку 2 страницы: штрихкод + карточка)
- Выход: ticket_sorted.pdf
Логика:
  1) По заголовкам на странице 1 находим x-границы столбцов. Нас интересует «Артикул».
  2) Для каждой отгрузки извлекаем ПОЛНОЕ значение «Артикул»: собираем все токены в границах колонки
     в вертикальном диапазоне ±12 px от базовой строки «Номер отправления», группируем по y, склеиваем сверху-вниз, слева-направо.
  3) В тикете ищем страницы по номеру отправления, забираем все страницы (обычно 2).
  4) Группируем отгрузки по «Артикулу» (алфавит), внутри — в порядке появления в сборочном листе.
  5) Формируем порядок страниц, вставляем разделители: «Артикул: … / Количество: …».
  6) Сохраняем PDF.
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict, OrderedDict
from pathlib import Path

import fitz  # PyMuPDF


OZON_SHIP_RE = re.compile(r"\b\d{6,}-\d{3,5}-\d\b")  # пример: 58678967-0003-4


# ---------- Утилиты для извлечения колонок и текста ----------
def detect_columns_from_header(doc: fitz.Document) -> dict[str, float]:
    """
    Возвращает словарь {имя_колонки: x0_минимальный} по первой странице документа.
    """
    page0 = doc[0]
    words = sorted(page0.get_text("words"), key=lambda w: (w[1], w[0]))
    header_tokens = {"№", "Номер", "отправления", "Фото", "Товар", "Артикул", "Кол-во", "Этикетка"}

    name_to_x: dict[str, float] = {}
    for w in words:
        txt = w[4]
        if txt in header_tokens:
            name_to_x[txt] = min(name_to_x.get(txt, w[0]), w[0])

    # Нормализуем «Номер отправления»
    if "Номер" in name_to_x and "отправления" in name_to_x:
        name_to_x["Номер отправления"] = min(name_to_x["Номер"], name_to_x["отправления"])

    # Возвращаем только цельные имена колонок в порядке по x
    cols = {
        "№": name_to_x.get("№", 0.0),
        "Номер отправления": name_to_x.get("Номер отправления", 0.0),
        "Фото": name_to_x.get("Фото", 0.0),
        "Товар": name_to_x.get("Товар", 0.0),
        "Артикул": name_to_x.get("Артикул", 0.0),
        "Кол-во": name_to_x.get("Кол-во", 0.0),
        "Этикетка": name_to_x.get("Этикетка", 0.0),
    }
    return dict(sorted(cols.items(), key=lambda kv: kv[1]))


def column_bounds(x_cols: dict[str, float], name: str) -> tuple[float, float]:
    """
    По словарю {col: x0} считает границы колонки по серединам между соседями.
    """
    names = list(x_cols.keys())
    xs = list(x_cols.values())
    mids = []
    for i in range(len(xs) - 1):
        mids.append((xs[i] + xs[i + 1]) / 2.0)

    idx = names.index(name)
    left = float("-inf") if idx == 0 else mids[idx - 1]
    right = float("inf") if idx == len(xs) - 1 else mids[idx]
    return left, right


def normalize_text(s: str) -> str:
    s = re.sub(r"\s+([,.)»”])", r"\1", s)
    s = re.sub(r"([«“(])\s+", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


# ---------- Извлечение полного «Артикула» ----------
def extract_full_artikul_map(asm_pdf: Path, y_band: float = 12.0) -> tuple[list[str], dict[str, str]]:
    """
    Возвращает:
      - ship_order: список номеров отправлений в порядке обхода таблицы
      - art_by_ship: {номер_отправления -> полный_артикул} (склейка по строкам сверху-вниз)
    """
    doc = fitz.open(asm_pdf)
    x_cols = detect_columns_from_header(doc)
    art_left, art_right = column_bounds(x_cols, "Артикул")

    ship_order: list[str] = []
    art_by_ship: dict[str, str] = OrderedDict()

    for page in doc:
        words = [tuple(w) for w in page.get_text("words")]
        words_sorted = sorted(words, key=lambda w: (w[1], w[0]))

        # Предвыборка слов в колонке «Артикул»
        art_words = [w for w in words_sorted if (art_left <= w[0] < art_right and w[4].strip())]

        # Проход по строкам с номерами отправлений
        for w in words_sorted:
            t = w[4].strip()
            if not OZON_SHIP_RE.fullmatch(t):
                continue

            ship = t
            ship_order.append(ship)
            y_s = w[1]

            # Слова в пределах вертикальной полосы вокруг строки отправления
            band = [aw for aw in art_words if abs(aw[1] - y_s) <= y_band]
            if not band:
                art_by_ship[ship] = "—"
                continue

            # Группируем по y (округление до 0.1), сортируем строки сверху-вниз, внутри — слева-направо
            lines: dict[float, list[tuple]] = {}
            for aw in band:
                yk = round(aw[1], 1)
                lines.setdefault(yk, []).append(aw)

            tokens: list[str] = []
            for _, arr in sorted(lines.items(), key=lambda kv: kv[0]):
                for x0, y0, x1, y1, txt, *_ in sorted(arr, key=lambda a: (a[0], a[1])):
                    tt = txt.strip()
                    if tt:
                        tokens.append(tt)

            text = normalize_text(" ".join(tokens))
            if len(text) <= 2:
                text = "—"

            art_by_ship[ship] = text

    doc.close()
    return ship_order, art_by_ship


# ---------- Карта страниц тикета по номеру отправления ----------
def map_ticket_pages(ticket_pdf: Path) -> dict[str, list[int]]:
    """
    Возвращает {номер_отправления -> [индексы_страниц]}.
    Обычно на отправление 2 страницы.
    """
    doc = fitz.open(ticket_pdf)
    ship_to_pages: dict[str, list[int]] = defaultdict(list)
    for i, page in enumerate(doc):
        text = page.get_text("text")
        ships = OZON_SHIP_RE.findall(text)
        for s in ships:
            if i not in ship_to_pages[s]:
                ship_to_pages[s].append(i)
    doc.close()
    return ship_to_pages


# ---------- Сборка итогового PDF ----------
def build_pdf_wbstyle(
    asm_pdf: Path,
    ticket_pdf: Path,
    out_pdf: Path,
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
) -> None:
    ship_order, art_by_ship = extract_full_artikul_map(asm_pdf, y_band=12.0)
    ship_to_pages = map_ticket_pages(ticket_pdf)

    # Группы: по полному «Артикулу» (алфавит), внутри — порядок появления в сборочном листе
    by_art: dict[str, list[str]] = defaultdict(list)
    for s in ship_order:
        by_art[art_by_ship.get(s, "—")].append(s)
    arts_sorted = sorted(by_art.keys(), key=lambda x: (x is None, str(x)))

    # Формируем список индексов страниц
    ticket_doc = fitz.open(ticket_pdf)
    total_pages = len(ticket_doc)
    ticket_doc.close()

    ordered_indices: list[int] = []
    group_meta: list[tuple[int, str, int]] = []  # (insert_at_before_offset, art, count_shipments)

    for art in arts_sorted:
        ships = by_art[art]
        group_meta.append((len(ordered_indices), art, len(ships)))
        for s in ships:
            for p in ship_to_pages.get(s, []):
                ordered_indices.append(p)

    # Добавим «молчащие» страницы (если есть) в хвост, чтобы итог совпадал по объёму
    used = set(ordered_indices)
    leftovers = [i for i in range(total_pages) if i not in used]
    ordered_indices.extend(leftovers)

    # Сохраняем с разделителями
    doc = fitz.open(ticket_pdf)
    doc.select(ordered_indices)

    font_name = "DejaVuSans"
    # (PyMuPDF сам загрузит шрифт по fontfile=)
    # Вставляем разделители, учитывая сдвиг
    offset = 0
    for insert_idx, art, cnt in group_meta:
        insert_at = min(insert_idx + offset, len(doc))
        page = doc.new_page(
            pno=insert_at, width=doc[0].rect.width, height=doc[0].rect.height
        )
        text = f"Артикул: {art}\nКоличество: {cnt}"
        page.insert_textbox(
            rect=page.rect,
            buffer=text,
            fontsize=12,
            fontname=font_name,
            fontfile=font_path,
            color=(0, 0, 0),
            align=0,  # влево — меньше шансов «ломать» длинные строки
        )
        offset += 1

    # финально
    doc.save(out_pdf, garbage=4)
    doc.close()


# ---------- CLI ----------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ozon ticket → WB-style сортировка по полному «Артикулу» (v9).")
    p.add_argument("--assembly", required=True, type=Path, help="Путь к PDF сборочного листа (assembly_list.pdf)")
    p.add_argument("--ticket", required=True, type=Path, help="Путь к PDF стикетов (ticket.pdf)")
    p.add_argument("--out", required=True, type=Path, help="Куда сохранить отсортированный PDF")
    p.add_argument("--font", default="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", help="TTF-шрифт для разделителей")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    build_pdf_wbstyle(args.assembly, args.ticket, args.out, font_path=args.font)


if __name__ == "__main__":
    main()
