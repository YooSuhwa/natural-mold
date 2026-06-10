---
name: pptx-presentation
description: Generate PPTX presentation artifacts from structured JSON using the Moldy Node skill runner.
version: 0.1.0
---

# PPTX Presentation

Use this skill to create a `.pptx` presentation from a small structured deck spec.

## Input

Prepare a JSON file with:

- `title`: deck title
- `subtitle`: short subtitle
- `slides`: array of slides with `title`, optional `body`, and optional `bullets`

Use `examples/e2e-pptx.json` when the user asks for a sample or E2E deck.

## Run

Read this `SKILL.md` first, then run:

```bash
node scripts/create_pptx.cjs --input examples/e2e-pptx.json --output moldy-pptx-demo.pptx
```

The script writes the file into `$OUTPUTS_DIR`. After running, tell the user the generated file name.
