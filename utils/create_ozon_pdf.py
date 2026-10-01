"""Read Ozon assembly rows and group intact ticket pages by full article."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import fitz


DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
OZON_SHIP_RE = re.compile(r"(?<!\w)\d{6,}-\d{3,5}-\d+(?!\w)")
OZON_SHIP_LOOSE_RE = re.compile(
    r"(?<!\w)\d{1,6}\s+\d{1,6}\s*-\s*\d{3,5}\s*-\s*\d+(?!\w)"
)
OZON_SHIP_SPACED_RE = re.compile(r"(?<!\w)\d{6,}\s*-\s*\d{3,5}\s*-\s*\d+(?!\w)")
OZON_LABEL_RE = re.compile(r"(?<!\w)ii\d+(?!\w)")
# The current label renders seven prefix digits and its four-digit suffix
# separately. Bound both pieces so unrelated numbers cannot be appended.
OZON_LABEL_SPLIT_RE = re.compile(r"(?<!\w)ii\d{7}\s+\d{4}(?!\w)")


class OzonValidationError(ValueError):
    """Invalid or ambiguous input, with a message safe to show in Telegram."""


@dataclass(frozen=True)
class OzonProcessingResult:
    success: bool
    error_message: str | None = None


@dataclass(frozen=True)
class AssemblyRow:
    shipment: str
    label: str | None
    article: str


def _merge_matches(text: str, strict: re.Pattern, loose: re.Pattern) -> tuple[list[str], int]:
    """Prefer complete split identifiers over their truncated strict suffixes."""
    spans = list(loose.finditer(text))
    matches = [(m.start(), re.sub(r"\s+", "", m.group())) for m in spans]
    matches.extend(
        (m.start(), m.group())
        for m in strict.finditer(text)
        if not any(s.start() <= m.start() and m.end() <= s.end() for s in spans)
    )
    return list(dict.fromkeys(value for _, value in sorted(matches))), len(spans)


def _find_ships_in_text(text: str) -> tuple[list[str], int]:
    # FBS warehouse numbers are not part of a split shipment prefix.
    text = re.sub(r"(?m)^FBS:[^\S\n]*\d+[^\S\n]*$", "", text)
    ships, n_split = _merge_matches(text, OZON_SHIP_SPACED_RE, OZON_SHIP_LOOSE_RE)
    return list(dict.fromkeys(re.sub(r"\s+", "", ship) for ship in ships)), n_split


def _find_labels_in_text(text: str) -> list[str]:
    return _merge_matches(text, OZON_LABEL_RE, OZON_LABEL_SPLIT_RE)[0]


def _detect_columns_from_header(doc: fitz.Document) -> dict[str, float]:
    if not len(doc):
        raise OzonValidationError("Сборочный лист пуст.")
    page = doc[0]
    words = page.get_text("words")
    anchors = [w for w in words if w[4] == "Артикул"]
    names = ("№", "Номер", "Фото", "Товар", "Артикул", "Кол-во", "Этикетка")
    for anchor in anchors:
        band = fitz.Rect(0, max(0, anchor[1] - 12), page.rect.width, anchor[3] + 12)
        cols = {}
        for name in names:
            # search_for also finds Номер inside the merged token №Номер.
            hits = page.search_for(name, clip=band)
            if hits:
                cols[name] = min(rect.x0 for rect in hits)
        if len(cols) == len(names) and all(cols[a] < cols[b] for a, b in zip(names, names[1:])):
            cols["Номер отправления"] = cols.pop("Номер")
            return dict(sorted(cols.items(), key=lambda item: item[1]))
    raise OzonValidationError("Не удалось определить столбцы сборочного листа. Пришлите исходный PDF Ozon.")


def _column_bounds(cols: dict[str, float], name: str) -> tuple[float, float]:
    names = list(cols)
    index = names.index(name)
    return cols[name], cols[names[index + 1]] if index + 1 < len(names) else float("inf")


def _normalize_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+([,.)»”])", r"\1", value)
    value = re.sub(r"([«“(])\s+", r"\1", value)
    value = re.sub(r'(\s|^)"\s+(?=\S)', r'\1"', value)
    value = re.sub(r'"([^"]*)"', lambda match: '"' + match.group(1).strip() + '"', value)
    return re.sub(r"\s{2,}", " ", value).strip()


def _join_words(words: list[tuple]) -> str:
    lines: dict[float, list[tuple]] = defaultdict(list)
    for word in words:
        lines[round(word[1], 1)].append(word)
    return "\n".join(
        " ".join(w[4] for w in sorted(line, key=lambda w: w[0]))
        for _, line in sorted(lines.items())
    )


def _table_separators(page: fitz.Page, cols: dict[str, float]) -> list[float]:
    """Merge adjacent header segments as well as full-width row rules."""
    segments: dict[float, list[tuple[float, float]]] = defaultdict(list)
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                a, b = item[1:]
                if abs(a.y - b.y) < 0.1:
                    segments[round(a.y, 1)].append(tuple(sorted((a.x, b.x))))
    separators = []
    for y, intervals in sorted(segments.items()):
        merged: list[list[float]] = []
        for left, right in sorted(intervals):
            if merged and left <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], right)
            else:
                merged.append([left, right])
        if any(left <= cols["№"] and right >= cols["Этикетка"] for left, right in merged):
            separators.append(y)
    return separators


def _extract_assembly_rows(asm_pdf: Path) -> list[AssemblyRow]:
    with fitz.open(asm_pdf) as doc:
        cols = _detect_columns_from_header(doc)
        ship_left, ship_right = _column_bounds(cols, "Номер отправления")
        art_left, art_right = _column_bounds(cols, "Артикул")
        rows: list[AssemblyRow] = []
        seen_ships: set[str] = set()
        seen_labels: set[str] = set()
        pending: list[list[tuple]] = []
        for page_number, page in enumerate(doc, 1):
            words = page.get_text("words")
            separators = _table_separators(page, cols)
            if not separators:
                raise OzonValidationError(f"Не удалось определить строки сборочного листа на странице {page_number}.")
            # Continuation pages have no top border. An unfinished bottom
            # row may also continue above the next page's first rule.
            boundaries = separators if page_number == 1 else [page.rect.y0, *separators]
            boundaries = [*boundaries, page.rect.y1]
            consumed: set[tuple] = set()
            for top, bottom in zip(boundaries, boundaries[1:]):
                row_words = [w for w in words if top < (w[1] + w[3]) / 2 < bottom]
                ship_words = [w for w in row_words if ship_left <= w[0] < ship_right]
                art_words = [w for w in row_words if art_left <= w[0] < art_right]
                # A repeated column header is not an order row.
                if any(w[4] == "Артикул" for w in row_words) and any(w[4] == "Кол-во" for w in row_words):
                    continue
                if not ship_words and not art_words and not pending:
                    continue
                consumed.update(ship_words)
                fragments = [*pending, row_words]
                if bottom == page.rect.y1:
                    pending = fragments
                    continue
                pending = []
                text = "\n".join(
                    _join_words([w for w in fragment if ship_left <= w[0] < ship_right])
                    for fragment in fragments
                )
                shipments, _ = _find_ships_in_text(text)
                labels = _find_labels_in_text(text)
                if len(shipments) != 1 or len(labels) > 1:
                    raise OzonValidationError(
                        f"Не удалось однозначно прочитать отправление в сборочном листе на странице {page_number}."
                    )
                shipment = shipments[0]
                label = labels[0] if labels else None
                article = _normalize_text("\n".join(
                    _join_words([w for w in fragment if art_left <= w[0] < art_right])
                    for fragment in fragments
                ))
                if not article or article in {"—", "-"}:
                    raise OzonValidationError(f"У отправления {shipment} не указан артикул.")
                if shipment in seen_ships or (label is not None and label in seen_labels):
                    raise OzonValidationError("В сборочном листе повторяется номер отправления или этикетки.")
                rows.append(AssemblyRow(shipment, label, article))
                seen_ships.add(shipment)
                if label:
                    seen_labels.add(label)
            unconsumed = [w for w in words if ship_left <= w[0] < ship_right and w not in consumed]
            text = _join_words(unconsumed)
            if _find_ships_in_text(text)[0] or _find_labels_in_text(text):
                raise OzonValidationError(f"Не удалось определить границы всех строк на странице {page_number}.")
        if pending:
            raise OzonValidationError("Не удалось определить нижнюю границу последнего отправления сборочного листа.")
        count = re.search(r"Количество\s+отправлений:\s*(\d+)", doc[0].get_text())
        if not rows or (count and len(rows) != int(count.group(1))):
            raise OzonValidationError("Не удалось прочитать все отправления сборочного листа.")
        return rows


def _extract_full_artikul_map(asm_pdf: Path) -> tuple[list[str], dict[str, str]]:
    rows = _extract_assembly_rows(asm_pdf)
    return [row.shipment for row in rows], {row.shipment: row.article for row in rows}


def _map_ticket_pages(doc: fitz.Document, rows: list[AssemblyRow]) -> dict[str, list[int]]:
    by_ship = {row.shipment: row for row in rows}
    by_label = {row.label: row.shipment for row in rows if row.label}
    mapping: dict[str, list[int]] = defaultdict(list)
    for index, page in enumerate(doc):
        text = page.get_text()
        ships, _ = _find_ships_in_text(text)
        labels = _find_labels_in_text(text)
        if not ships and not labels:
            raise OzonValidationError(f"Не удалось распознать этикетку на странице {index + 1}.")
        if any(ship not in by_ship for ship in ships) or any(label not in by_label for label in labels):
            raise OzonValidationError(
                f"Этикетка на странице {index + 1} отсутствует в сборочном листе. "
                "Проверьте, что файлы относятся к одной сборке."
            )
        owners = set(ships) | {by_label[label] for label in labels}
        if len(owners) != 1:
            raise OzonValidationError(
                f"На странице {index + 1} указаны противоречивые номера отправления или этикетки."
            )
        mapping[owners.pop()].append(index)
    missing = set(by_ship) - set(mapping)
    if missing:
        raise OzonValidationError(
            f"В файле этикеток не найдены {len(missing)} отправления из сборочного листа. "
            "Пришлите полный комплект этикеток."
        )
    logging.info(
        "OZON: matched %d/%d shipments, %d/%d ticket pages",
        len(mapping), len(rows), sum(map(len, mapping.values())), len(doc),
    )
    return dict(mapping)


def _insert_separator(page: fitz.Page, article: str, count: int, font_path: str) -> None:
    text = f"Артикул: {article}\nКоличество: {count}"
    rect = page.rect + (3, 3, -3, -3)
    for fontsize in range(12, 5, -1):
        # Shape only commits after the entire textbox fits.
        shape = page.new_shape()
        remaining = shape.insert_textbox(
            rect, text, fontsize=fontsize, fontname="DejaVuSans", fontfile=font_path,
        )
        if remaining >= 0:
            shape.commit()
            return
    raise OzonValidationError(
        "Название артикула не помещается на разделителе даже при уменьшении шрифта. Проверьте сборочный лист."
    )


def _build_pdf_wbstyle(asm_pdf: Path, ticket_pdf: Path, out_pdf: Path, font_path: str = DEFAULT_FONT) -> None:
    rows = _extract_assembly_rows(asm_pdf)
    groups: dict[str, list[AssemblyRow]] = defaultdict(list)
    for row in rows:
        groups[row.article].append(row)
    with fitz.open(ticket_pdf) as doc:
        mapping = _map_ticket_pages(doc, rows)
        indices: list[int] = []
        separators = []
        for article, group in sorted(groups.items()):
            separators.append((len(indices), article, len(group)))
            for row in group:
                indices.extend(mapping[row.shipment])
        doc.select(indices)
        for offset, (index, article, count) in enumerate(separators):
            # Match the dimensions of this group's first ticket page.
            rect = doc[index + offset].rect
            page = doc.new_page(pno=index + offset, width=rect.width, height=rect.height)
            _insert_separator(page, article, count, font_path)
        doc.save(out_pdf, garbage=4)
    logging.info("OZON: saved %d article groups, %d original pages", len(groups), len(indices))


async def process_ozon_files(
    assembly_pdf_path: str,
    ticket_pdf_path: str,
    output_pdf_path: str,
    font_path: str = DEFAULT_FONT,
) -> OzonProcessingResult:
    try:
        _build_pdf_wbstyle(
            Path(assembly_pdf_path), Path(ticket_pdf_path), Path(output_pdf_path), font_path=font_path,
        )
        return OzonProcessingResult(True)
    except OzonValidationError as exc:
        logging.warning("OZON: input rejected: %s", exc)
        return OzonProcessingResult(False, str(exc))
    except Exception:
        logging.exception("OZON: processing failed")
        return OzonProcessingResult(
            False,
            "Не удалось обработать PDF Ozon. Проверьте, что отправлены исходные PDF "
            "сборочного листа и этикеток, и попробуйте снова.",
        )
