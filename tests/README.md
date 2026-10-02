# Ozon PDF regression checks

Run from the repository root with the existing project dependencies installed:

```bash
venv/bin/python -m unittest discover -s tests -v
```

The suite generates its PDF inputs in temporary directories. It does not require
customer PDFs, database access, a bot token, or network access. Telegram handler
checks mock the bot and its dispatcher before importing the handler.

Covered behavior includes complete label IDs (including split text and identical
four-digit suffixes), legacy shipment IDs and multiple ticket pages, multiline
articles, table rules and page continuations, conflicting or missing matches,
separator text fitting, the shared CLI, and Telegram file/state cleanup.

The CLI uses the same processor as the bot:

```bash
venv/bin/python test_code_ozon.py --assembly assembly.pdf --ticket ticket.pdf --out sorted.pdf
```

Exit status is 0 on success, 2 for invalid or ambiguous input, and 1 for a technical
failure. The async bot interface returns `OzonProcessingResult` with `success`
and a user-safe `error_message` on failure.

Local acceptance checks for this change used the customer files dated 2026-10-01
and the existing legacy examples. They matched 37 shipments / 37 ticket pages
and 272 shipments / 544 ticket pages respectively. Every original page appeared
exactly once, with identical rendered pixels at 144 dpi. All separator titles,
shipment counts, group boundaries, and page dimensions were checked; every
separator was also inspected visually. Customer PDFs remain outside Git.

When a shipment has several product articles, the full article cell is joined in
reading order and the shipment stays together. This also applies to an assembly
row continuing onto the next page. Separator counts represent shipments, not
product quantities or ticket page counts.
