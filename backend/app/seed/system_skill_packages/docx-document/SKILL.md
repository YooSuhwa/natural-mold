---
name: docx-document
description: Generate DOCX document artifacts from structured JSON using the Moldy Node skill runner.
version: 0.1.0
---

# DOCX Document

Use this skill to create a `.docx` report from structured content.

## Input

Prepare a JSON file with:

- `title`: document title
- `subtitle`: short subtitle
- `sections`: array of sections with `heading`, `paragraphs`, and optional `bullets`
- `table`: optional table with `headers` and `rows`

Use `examples/e2e-docx.json` when the user asks for a sample or E2E document.

## Run

Read this `SKILL.md` first, then run:

```bash
node scripts/create_docx.cjs --input examples/e2e-docx.json --output moldy-docx-demo.docx
```

The script writes the file into `$OUTPUTS_DIR`. After running, tell the user the generated file name.
