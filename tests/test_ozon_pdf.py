"""Synthetic PDF regressions; no customer files or Telegram credentials needed."""

import asyncio
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

import fitz

from utils.create_ozon_pdf import (
    DEFAULT_FONT,
    AssemblyRow,
    OzonProcessingResult,
    OzonValidationError,
    _build_pdf_wbstyle,
    _extract_assembly_rows,
    _find_labels_in_text,
    _find_ships_in_text,
    _insert_separator,
    _map_ticket_pages,
    _normalize_text,
    process_ozon_files,
)

ROOT = Path(__file__).resolve().parents[1]
SHIP_A = "12345678-0001-1"
SHIP_B = "87654321-0002-26"
SHIP_C = "0134567890-0003-2"
LABEL_A = "ii50063232117"
LABEL_B = "ii60063232117"  # Deliberately the same four-digit suffix.
LABEL_C = "ii50063232776"
COLS = [(31, "№"), (45, "Номер"), (144, "Фото"), (189, "Товар"),
        (402, "Артикул"), (487, "Кол-во"), (527, "Этикетка")]


def write_text(page, x, y, text, size=8):
    page.insert_text((x, y), text, fontsize=size, fontname="DejaVuSans", fontfile=DEFAULT_FONT)


def draw_rule(page, y):
    page.draw_line((28, y), (575, y))


def make_assembly(path, rows, *, headers=True, rules=True, omit_rules=(),
                  declared_count=None, rows_per_page=8, merged_header=False):
    with fitz.open() as doc:
        for index, (shipment, label, lines) in enumerate(rows):
            if index % rows_per_page == 0:
                page = doc.new_page(width=595.28, height=841.89)
                top = 130 if index == 0 else 25
                if index == 0:
                    count = len(rows) if declared_count is None else declared_count
                    write_text(page, 28, 35, f"Количество отправлений: {count}", 12)
                    if headers:
                        for x, name in COLS:
                            if merged_header and name == "Номер":
                                continue
                            write_text(page, x, 110, "№Номер" if merged_header and name == "№" else name)
                    if rules:
                        # Like Ozon, draw the header rule as contiguous segments.
                        for a, b in zip([28, 42, 141, 187, 399, 484, 524], [42, 141, 187, 399, 484, 524, 575]):
                            page.draw_line((a, top), (b, top))
            write_text(page, 31, top + 30, str(index + 1))
            write_text(page, 45, top + 30, shipment)
            if label:
                write_text(page, 45, top + 44, label)
            write_text(page, 189, top + 30, 'Product "unrelated"')
            for n, line in enumerate(lines):
                write_text(page, 402, top + 16 + 12 * n, line)
            write_text(page, 487, top + 30, "1")
            write_text(page, 527, top + 30, "2117")
            top += 70
            if rules and index not in omit_rules:
                draw_rule(page, top)
        if not rows:
            page = doc.new_page()
            write_text(page, 28, 35, "Количество отправлений: 0")
        doc.save(path)


def make_ticket(path, texts, *, size=(164.25, 113.25)):
    with fitz.open() as doc:
        for i, text in enumerate(texts):
            page = doc.new_page(width=size[0], height=size[1])
            page.insert_text((8, 25), text, fontsize=8)
            page.draw_rect(fitz.Rect(10 + i, 80, 30 + i, 95), fill=(0, 0, 0))
        doc.save(path)


def pixel_hash(page):
    return hashlib.sha256(page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).samples).hexdigest()


class PdfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.asm = self.base / "assembly.pdf"
        self.ticket = self.base / "ticket.pdf"
        self.out = self.base / "sorted.pdf"

    def reject(self, rows, tickets, message, **kwargs):
        make_assembly(self.asm, rows, **kwargs)
        make_ticket(self.ticket, tickets)
        with self.assertRaisesRegex(OzonValidationError, message):
            _build_pdf_wbstyle(self.asm, self.ticket, self.out)
        self.assertFalse(self.out.exists())

    def test_split_and_legacy_identifiers_keep_complete_prefix_and_suffix(self):
        for text in ["1234 567890-0001-26", "1234\n567890 - 0001 - 26"]:
            with self.subTest(text=text):
                self.assertEqual(_find_ships_in_text(text), (["1234567890-0001-26"], 1))
        self.assertEqual(_find_ships_in_text(SHIP_B)[0], [SHIP_B])
        self.assertEqual(_find_ships_in_text(f"{SHIP_B}\n1234 567890-0001-26")[0],
                         [SHIP_B, "1234567890-0001-26"])

    def test_warehouse_number_cannot_become_split_prefix(self):
        self.assertEqual(_find_ships_in_text("FBS: 240959\n123456-0001-26")[0], ["123456-0001-26"])

    def test_full_and_split_label_ids(self):
        for text in [LABEL_A, "ii5006323 2117", "ii5006323\n2117", "ii5006323\t2117"]:
            with self.subTest(text=text):
                self.assertEqual(_find_labels_in_text(text), [LABEL_A])
        self.assertEqual(_find_labels_in_text(f"{LABEL_A}\n{LABEL_B}"), [LABEL_A, LABEL_B])
        self.assertEqual(_find_labels_in_text("wordii50063232117"), [])

    def test_new_labels_same_suffix_group_and_preserve_original_pixels(self):
        rows = [(SHIP_A, LABEL_A, ["Beta"]), (SHIP_B, LABEL_B, ["Alpha"]),
                (SHIP_C, LABEL_C, ["Alpha"])]
        make_assembly(self.asm, rows)
        make_ticket(self.ticket, [LABEL_C, "ii5006323\n2117", "ii6006323 2117"])
        _build_pdf_wbstyle(self.asm, self.ticket, self.out)
        with fitz.open(self.ticket) as original, fitz.open(self.out) as result:
            self.assertEqual(len(result), 5)
            self.assertIn("Артикул: Alpha", result[0].get_text())
            self.assertIn("Количество: 2", result[0].get_text())
            self.assertIn("Артикул: Beta", result[3].get_text())
            self.assertIn("Количество: 1", result[3].get_text())
            self.assertEqual([pixel_hash(result[i]) for i in [1, 2, 4]],
                             [pixel_hash(original[i]) for i in [2, 0, 1]])
            self.assertEqual(result[1].rect, original[2].rect)

    def test_legacy_multiple_pages_and_multidigit_suffix(self):
        make_assembly(self.asm, [(SHIP_A, None, ["Beta"]), (SHIP_B, None, ["Alpha"])])
        make_ticket(self.ticket, [SHIP_A, SHIP_B, SHIP_A, SHIP_B])
        _build_pdf_wbstyle(self.asm, self.ticket, self.out)
        with fitz.open(self.ticket) as original, fitz.open(self.out) as result:
            self.assertEqual(len(result), 6)
            self.assertIn("Количество: 1", result[0].get_text())
            self.assertEqual([pixel_hash(result[i]) for i in [1, 2, 4, 5]],
                             [pixel_hash(original[i]) for i in [1, 3, 0, 2]])

    def test_mixed_formats_and_both_identifiers_agree(self):
        make_assembly(self.asm, [(SHIP_A, LABEL_A, ["A"]), (SHIP_B, LABEL_B, ["B"])])
        make_ticket(self.ticket, [f"{SHIP_A}\n{LABEL_A}", LABEL_B, SHIP_B])
        with fitz.open(self.ticket) as doc:
            self.assertEqual(_map_ticket_pages(doc, _extract_assembly_rows(self.asm)),
                             {SHIP_A: [0], SHIP_B: [1, 2]})

    def test_full_multiline_articles_keep_quotes_color_and_chapter(self):
        rows = [(SHIP_A, LABEL_A, ["Книга", "воспоминаний", "ГЛАВА 2"]),
                (SHIP_B, LABEL_B, ["Фотоальбом", '"Сердечный"', "розовый"])]
        make_assembly(self.asm, rows)
        actual = _extract_assembly_rows(self.asm)
        self.assertEqual(actual[0].article, "Книга воспоминаний ГЛАВА 2")
        self.assertEqual(actual[1].article, 'Фотоальбом "Сердечный" розовый')
        self.assertNotIn("unrelated", actual[0].article)

    def test_standalone_quotes_remain_attached_to_their_article(self):
        self.assertEqual(_normalize_text('Фотоальбом " \n Я тебя люблю \n "'),
                         'Фотоальбом "Я тебя люблю"')

    def test_continuation_pages_without_upper_border(self):
        rows = [(SHIP_A, LABEL_A, ["Alpha"]), (SHIP_B, LABEL_B, ["Beta"])]
        make_assembly(self.asm, rows, rows_per_page=1)
        self.assertEqual([r.shipment for r in _extract_assembly_rows(self.asm)], [SHIP_A, SHIP_B])

    def test_merged_number_header_is_supported(self):
        make_assembly(self.asm, [(SHIP_A, LABEL_A, ["Alpha"])], merged_header=True)
        self.assertEqual(_extract_assembly_rows(self.asm)[0].shipment, SHIP_A)

    def test_order_row_can_continue_across_pages(self):
        make_assembly(self.asm, [(SHIP_A, LABEL_A, ["First"])], omit_rules={0})
        with fitz.open(self.asm) as doc:
            page = doc.new_page(width=595.28, height=841.89)
            write_text(page, 402, 40, "Second")
            draw_rule(page, 65)
            replacement = self.base / "continued.pdf"
            doc.save(replacement)
        self.assertEqual(_extract_assembly_rows(replacement), [AssemblyRow(SHIP_A, LABEL_A, "First Second")])

    def test_split_shipment_in_assembly_is_not_prefixed_with_row_number(self):
        make_assembly(self.asm, [("1234 567890-0001-26", LABEL_A, ["A"])])
        self.assertEqual(_extract_assembly_rows(self.asm)[0].shipment, "1234567890-0001-26")

    def test_multiple_articles_in_one_shipment_stay_in_row_order(self):
        make_assembly(self.asm, [(SHIP_A, LABEL_A, ["First", "Second", "Third"])])
        self.assertEqual(_extract_assembly_rows(self.asm)[0].article, "First Second Third")

    def test_missing_ticket_is_rejected(self):
        self.reject([(SHIP_A, LABEL_A, ["A"]), (SHIP_B, LABEL_B, ["B"])], [LABEL_A], "не найдены 1")

    def test_foreign_labels_and_shipments_are_rejected(self):
        for text in [LABEL_B, SHIP_B, f"{SHIP_A}\n{LABEL_B}"]:
            with self.subTest(text=text):
                self.reject([(SHIP_A, LABEL_A, ["A"])], [text], "отсутствует")

    def test_unrecognized_page_is_rejected(self):
        self.reject([(SHIP_A, LABEL_A, ["A"])], [LABEL_A, "QR only"], "странице 2")

    def test_conflicting_identifiers_and_two_shipments_on_page_are_rejected(self):
        rows = [(SHIP_A, LABEL_A, ["A"]), (SHIP_B, LABEL_B, ["B"])]
        for text in [f"{SHIP_A}\n{LABEL_B}", f"{SHIP_A}\n{SHIP_B}", f"{LABEL_A}\n{LABEL_B}"]:
            with self.subTest(text=text):
                self.reject(rows, [text], "противоречивые")

    def test_duplicate_assembly_identifiers_are_rejected(self):
        for second in [(SHIP_A, LABEL_B, ["B"]), (SHIP_B, LABEL_A, ["B"])]:
            with self.subTest(second=second):
                self.reject([(SHIP_A, LABEL_A, ["A"]), second], [LABEL_A], "повторяется")

    def test_empty_article_is_rejected(self):
        for lines in [[], ["—"], ["-"]]:
            with self.subTest(lines=lines):
                self.reject([(SHIP_A, LABEL_A, lines)], [LABEL_A], "не указан артикул")

    def test_missing_header_is_rejected(self):
        self.reject([(SHIP_A, LABEL_A, ["A"])], [LABEL_A], "столбцы", headers=False)

    def test_missing_row_rules_are_rejected(self):
        self.reject([(SHIP_A, LABEL_A, ["A"])], [LABEL_A], "строки", rules=False)

    def test_missing_rule_between_orders_is_ambiguous(self):
        self.reject([(SHIP_A, LABEL_A, ["A"]), (SHIP_B, LABEL_B, ["B"])],
                    [LABEL_A, LABEL_B], "однозначно", omit_rules={0})

    def test_unclosed_last_order_is_rejected(self):
        self.reject([(SHIP_A, LABEL_A, ["A"])], [LABEL_A], "нижнюю границу", omit_rules={0})

    def test_declared_count_must_match_parsed_orders(self):
        self.reject([(SHIP_A, LABEL_A, ["A"])], [LABEL_A], "все отправления", declared_count=2)

    def test_separator_font_shrinks_and_keeps_all_text(self):
        with fitz.open() as doc:
            page = doc.new_page(width=120, height=55)
            article = "Книга воспоминаний ГЛАВА 2"
            _insert_separator(page, article, 3, DEFAULT_FONT)
            self.assertIn(article, " ".join(page.get_text().split()))
            self.assertIn("Количество: 3", " ".join(page.get_text().split()))
            sizes = [s["size"] for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]]
            self.assertTrue(all(6 <= size < 12 for size in sizes))

    def test_separator_that_cannot_fit_is_rejected_without_committing_text(self):
        with fitz.open() as doc:
            page = doc.new_page(width=80, height=40)
            with self.assertRaisesRegex(OzonValidationError, "не помещается"):
                _insert_separator(page, "Длинное название " * 50, 1, DEFAULT_FONT)
            self.assertEqual(page.get_text(), "")

    def test_async_result_success_and_validation_message(self):
        make_assembly(self.asm, [(SHIP_A, LABEL_A, ["Alpha"])])
        make_ticket(self.ticket, [LABEL_A])
        result = asyncio.run(process_ozon_files(str(self.asm), str(self.ticket), str(self.out)))
        self.assertEqual(result, OzonProcessingResult(True))
        make_ticket(self.ticket, [LABEL_B])
        result = asyncio.run(process_ozon_files(str(self.asm), str(self.ticket), str(self.base / "bad.pdf")))
        self.assertFalse(result.success)
        self.assertIn("отсутствует", result.error_message)

    def test_technical_error_details_are_not_shown_to_user(self):
        with patch("utils.create_ozon_pdf._build_pdf_wbstyle", side_effect=RuntimeError("private server path")), self.assertLogs(level="ERROR"):
            result = asyncio.run(process_ozon_files("a", "b", "c"))
        self.assertFalse(result.success)
        self.assertNotIn("private server path", result.error_message)

    def test_cli_preserves_arguments_and_rejects_invalid_pair(self):
        make_assembly(self.asm, [(SHIP_A, LABEL_A, ["A"])])
        make_ticket(self.ticket, [LABEL_A])
        command = [sys.executable, str(ROOT / "test_code_ozon.py"), "--assembly", str(self.asm),
                   "--ticket", str(self.ticket), "--out", str(self.out), "--font", DEFAULT_FONT]
        completed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(self.out.exists())
        self.out.unlink()
        make_ticket(self.ticket, [LABEL_B])
        completed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("отсутствует", completed.stderr)
        self.assertFalse(self.out.exists())


class TelegramHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        previous = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, previous)
        self.asm = Path("assembly.pdf")
        self.asm.write_bytes(b"assembly fixture")
        self.bot = types.SimpleNamespace(send_document=AsyncMock(), edit_message_reply_markup=AsyncMock())
        decorator = lambda *args, **kwargs: lambda function: function
        setup = types.ModuleType("bot_setup")
        setup.bot = self.bot
        setup.dp = types.SimpleNamespace(callback_query_handler=decorator, message_handler=decorator)
        wb = types.ModuleType("utils.create_pdf")
        wb.process_files = AsyncMock()
        spec = importlib.util.spec_from_file_location("ozon_handler_under_test", ROOT / "handlers/sticker.py")
        self.handler = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"bot_setup": setup, "utils.create_pdf": wb}):
            spec.loader.exec_module(self.handler)
        self.state = types.SimpleNamespace(get_data=AsyncMock(return_value={"assembly_file": str(self.asm)}),
                                           update_data=AsyncMock(), finish=AsyncMock())

        async def download(*, destination_file):
            Path(destination_file).write_bytes(b"ticket fixture")

        self.message = types.SimpleNamespace(
            from_user=types.SimpleNamespace(id=42), answer=AsyncMock(),
            document=types.SimpleNamespace(mime_type="application/pdf", file_size=10,
                                           download=AsyncMock(side_effect=download)),
        )

    def assert_cleaned_up(self):
        self.assertFalse(self.asm.exists())
        self.assertFalse(Path("ozon_ticket_42.pdf").exists())
        self.assertFalse(Path("ozon_sorted_42.pdf").exists())
        self.state.finish.assert_awaited_once()

    async def test_validation_failure_does_not_send_pdf_and_cleans_stale_output(self):
        Path("ozon_sorted_42.pdf").write_bytes(b"stale result")
        result = OzonProcessingResult(False, "В файле этикеток не найдены 1 отправления")
        with patch.object(self.handler, "process_ozon_files", AsyncMock(return_value=result)):
            await self.handler.handle_ozon_ticket(self.message, self.state)
        self.bot.send_document.assert_not_awaited()
        self.assertEqual(self.message.answer.await_args_list[-1].args[0], result.error_message)
        self.assert_cleaned_up()

    async def test_success_sends_pdf_then_cleans_files_and_finishes_state(self):
        async def process(*args):
            Path(args[2]).write_bytes(b"sorted result")
            return OzonProcessingResult(True)
        with patch.object(self.handler, "process_ozon_files", AsyncMock(side_effect=process)):
            await self.handler.handle_ozon_ticket(self.message, self.state)
        self.bot.send_document.assert_awaited_once()
        self.assertEqual(self.message.answer.await_args_list[-1].args[0], "✅ Обработка завершена.")
        self.assert_cleaned_up()

    async def test_send_failure_still_cleans_files_and_finishes_state(self):
        Path("ozon_sorted_42.pdf").write_bytes(b"sorted result")
        self.bot.send_document.side_effect = RuntimeError("Telegram unavailable")
        with patch.object(self.handler, "process_ozon_files", AsyncMock(return_value=OzonProcessingResult(True))):
            with self.assertRaisesRegex(RuntimeError, "Telegram unavailable"):
                await self.handler.handle_ozon_ticket(self.message, self.state)
        self.assert_cleaned_up()

    async def test_download_failure_still_cleans_files_and_finishes_state(self):
        self.message.document.download.side_effect = RuntimeError("download failed")
        with self.assertRaisesRegex(RuntimeError, "download failed"):
            await self.handler.handle_ozon_ticket(self.message, self.state)
        self.assert_cleaned_up()

    async def test_processing_failure_still_cleans_files_and_finishes_state(self):
        with patch.object(self.handler, "process_ozon_files", AsyncMock(side_effect=RuntimeError("unexpected"))):
            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                await self.handler.handle_ozon_ticket(self.message, self.state)
        self.bot.send_document.assert_not_awaited()
        self.assert_cleaned_up()

    async def test_invalid_mime_keeps_state_for_retry(self):
        self.message.document.mime_type = "text/plain"
        await self.handler.handle_ozon_ticket(self.message, self.state)
        self.message.document.download.assert_not_awaited()
        self.bot.send_document.assert_not_awaited()
        self.state.finish.assert_not_awaited()
        self.assertTrue(self.asm.exists())


if __name__ == "__main__":
    unittest.main()
