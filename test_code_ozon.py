#!/usr/bin/env python3
"""Command-line entry point for the shared Ozon PDF processor."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from utils.create_ozon_pdf import DEFAULT_FONT, OzonValidationError, _build_pdf_wbstyle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Сортировка этикеток Ozon по полному артикулу.")
    parser.add_argument("--assembly", required=True, type=Path, help="PDF сборочного листа")
    parser.add_argument("--ticket", required=True, type=Path, help="PDF этикеток")
    parser.add_argument("--out", required=True, type=Path, help="Итоговый PDF")
    parser.add_argument("--font", default=DEFAULT_FONT, help="TTF-шрифт для разделителей")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args()
    try:
        _build_pdf_wbstyle(args.assembly, args.ticket, args.out, font_path=args.font)
    except OzonValidationError as exc:
        logging.error("%s", exc)
        raise SystemExit(2) from None
    except Exception:
        logging.exception("Не удалось обработать PDF Ozon")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
