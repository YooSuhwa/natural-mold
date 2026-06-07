---
name: patent-hwpx-generator
description: Generate Korean patent-style HWPX artifacts from structured JSON.
version: 0.1.0
---

# Korean Patent HWPX Generator

Use this skill to create a Korean `.hwpx` patent-style invention report from structured content.

## Input

Prepare a JSON file with:

- `date`: report date such as `2026. 06. 07.`
- `title`: invention title
- `abstract`: short invention summary
- `background`: background and problem statement
- `claims`: array of claim strings
- `inventors`: up to five inventors with `kor`, `eng`, `dept`, and `role`

Use `examples/e2e-patent.json` when the user asks for a sample or E2E patent document.

## Run

Read this `SKILL.md` first, then run:

```bash
python scripts/generate_hwpx.py --input examples/e2e-patent.json --output moldy-patent-demo.hwpx
```

The script writes the file into `$OUTPUTS_DIR`. After running, tell the user the generated file name.
