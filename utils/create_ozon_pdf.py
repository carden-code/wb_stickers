"""Utilities for processing Ozon sticker PDFs into WB-style grouped output."""

from __future__ import annotations

import logging
import re
from collections import defaultdict, OrderedDict
from pathlib import Path

import fitz  # PyMuPDF


OZON_SHIP_RE = re.compile(r"\b\d{6,}-\d{3,5}-\d\b")


def _detect_columns_from_header(doc: fitz.Document) -> dict[str, float]:
    """Return mapping of column names to their left x-coordinate from the first page."""
    page0 = doc[0]
    words = sorted(page0.get_text("words"), key=lambda w: (w[1], w[0]))
    header_tokens = {
        "№",
        "Номер",
        "отправления",
        "Фото",
        "Товар",
        "Артикул",
        "Кол-во",
        "Этикетка",
    }

    name_to_x: dict[str, float] = {}
    for w in words:
        txt = w[4]
        if txt in header_tokens:
            name_to_x[txt] = min(name_to_x.get(txt, w[0]), w[0])

    if "Номер" in name_to_x and "отправления" in name_to_x:
        name_to_x["Номер отправления"] = min(name_to_x["Номер"], name_to_x["отправления"])

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


def _column_bounds(x_cols: dict[str, float], name: str) -> tuple[float, float]:
    """Calculate left/right bounds for the column based on neighbours."""
    names = list(x_cols.keys())
    xs = list(x_cols.values())
    mids = [(xs[i] + xs[i + 1]) / 2.0 for i in range(len(xs) - 1)]

    idx = names.index(name)
    left = float("-inf") if idx == 0 else mids[idx - 1]
    right = float("inf") if idx == len(xs) - 1 else mids[idx]
    return left, right


def _normalize_text(value: str) -> str:
    """Cleanup whitespace artefacts inside joined tokens."""
    value = re.sub(r"\s+([,.)»”])", r"\1", value)
    value = re.sub(r"([«“(])\s+", r"\1", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip()


def _extract_full_artikul_map(asm_pdf: Path, y_band: float = 12.0) -> tuple[list[str], dict[str, str]]:
    doc = fitz.open(asm_pdf)
    try:
        x_cols = _detect_columns_from_header(doc)
        art_left, art_right = _column_bounds(x_cols, "Артикул")

        ship_order: list[str] = []
        art_by_ship: dict[str, str] = OrderedDict()

        for page in doc:
            words = [tuple(w) for w in page.get_text("words")]
            words_sorted = sorted(words, key=lambda w: (w[1], w[0]))
            art_words = [w for w in words_sorted if art_left <= w[0] < art_right and w[4].strip()]

            for w in words_sorted:
                token = w[4].strip()
                if not OZON_SHIP_RE.fullmatch(token):
                    continue

                ship = token
                ship_order.append(ship)
                y_s = w[1]

                band = [aw for aw in art_words if abs(aw[1] - y_s) <= y_band]
                if not band:
                    art_by_ship[ship] = "—"
                    continue

                lines: dict[float, list[tuple]] = {}
                for aw in band:
                    y_key = round(aw[1], 1)
                    lines.setdefault(y_key, []).append(aw)

                tokens: list[str] = []
                for _, arr in sorted(lines.items(), key=lambda kv: kv[0]):
                    for x0, y0, x1, y1, txt, *_ in sorted(arr, key=lambda a: (a[0], a[1])):
                        text = txt.strip()
                        if text:
                            tokens.append(text)

                text = _normalize_text(" ".join(tokens))
                art_by_ship[ship] = text if len(text) > 2 else "—"

        return ship_order, art_by_ship
    finally:
        doc.close()


def _map_ticket_pages(ticket_pdf: Path) -> dict[str, list[int]]:
    doc = fitz.open(ticket_pdf)
    try:
        ship_to_pages: dict[str, list[int]] = defaultdict(list)
        for i, page in enumerate(doc):
            text = page.get_text("text")
            ships = OZON_SHIP_RE.findall(text)
            for ship in ships:
                if i not in ship_to_pages[ship]:
                    ship_to_pages[ship].append(i)
        return ship_to_pages
    finally:
        doc.close()


def _build_pdf_wbstyle(
    asm_pdf: Path,
    ticket_pdf: Path,
    out_pdf: Path,
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
) -> None:
    ship_order, art_by_ship = _extract_full_artikul_map(asm_pdf, y_band=12.0)
    ship_to_pages = _map_ticket_pages(ticket_pdf)

    by_art: dict[str, list[str]] = defaultdict(list)
    for ship in ship_order:
        by_art[art_by_ship.get(ship, "—")].append(ship)
    arts_sorted = sorted(by_art.keys(), key=lambda x: (x is None, str(x)))

    ticket_doc = fitz.open(ticket_pdf)
    try:
        total_pages = len(ticket_doc)
    finally:
        ticket_doc.close()

    ordered_indices: list[int] = []
    group_meta: list[tuple[int, str, int]] = []

    for art in arts_sorted:
        ships = by_art[art]
        group_meta.append((len(ordered_indices), art, len(ships)))
        for ship in ships:
            ordered_indices.extend(ship_to_pages.get(ship, []))

    seen = set(ordered_indices)
    leftovers = [i for i in range(total_pages) if i not in seen]
    ordered_indices.extend(leftovers)

    doc = fitz.open(ticket_pdf)
    try:
        doc.select(ordered_indices)

        font_name = "DejaVuSans"
        offset = 0
        for insert_idx, art, count in group_meta:
            insert_at = min(insert_idx + offset, len(doc))
            page = doc.new_page(
                pno=insert_at,
                width=doc[0].rect.width,
                height=doc[0].rect.height,
            )
            text = f"Артикул: {art}\nКоличество: {count}"
            page.insert_textbox(
                rect=page.rect,
                buffer=text,
                fontsize=12,
                fontname=font_name,
                fontfile=font_path,
                color=(0, 0, 0),
                align=0,
            )
            offset += 1

        doc.save(out_pdf, garbage=4)
    finally:
        doc.close()


async def process_ozon_files(
    assembly_pdf_path: str,
    ticket_pdf_path: str,
    output_pdf_path: str,
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
) -> bool:
    """Convert Ozon assembly/ticket PDFs into grouped ticket output."""
    try:
        _build_pdf_wbstyle(Path(assembly_pdf_path), Path(ticket_pdf_path), Path(output_pdf_path), font_path=font_path)
        return True
    except Exception as exc:
        logging.error("Ошибка при обработке OZON файлов: %s", exc)
        return False

