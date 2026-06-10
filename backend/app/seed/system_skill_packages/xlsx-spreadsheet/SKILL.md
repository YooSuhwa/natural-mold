---
name: xlsx-spreadsheet
description: Generate XLSX spreadsheet artifacts from structured JSON using the Moldy Node skill runner.
version: 0.1.0
---

# XLSX Spreadsheet

Use this skill to create a `.xlsx` workbook from structured sheet data.

## Input

Prepare a JSON file with:

- `title`: workbook title
- `sheets`: array of sheets with `name`, `headers`, and `rows`

Use `examples/e2e-xlsx.json` when the user asks for a sample or E2E workbook.

## Run

Read this `SKILL.md` first, then run:

```bash
node scripts/create_xlsx.cjs --input examples/e2e-xlsx.json --output moldy-xlsx-demo.xlsx
```

The script writes the file into `$OUTPUTS_DIR`. After running, tell the user the generated file name.
