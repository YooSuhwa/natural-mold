# Document Skills And Artifact Viewers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the full development work that lets Moldy agents generate DOCX, XLSX, PPTX, and Korean patent HWPX files through built-in marketplace skills, preview those generated artifacts in the chat artifact rail with lightweight open-source client-side viewers, and prove the end-to-end flow with a real backend/frontend Playwright run plus verified screenshots.

**Architecture:** Keep the current deepagents runtime and marketplace skill packaging model. Extend `execute_in_skill` from `python|curl` to `python|node|curl` while preserving the no-shell, selected-skill-only, output-directory, timeout, credential-redaction, and artifact ingestion contracts. Add four system-seeded skill packages under `backend/app/seed/system_skill_packages`. Add four frontend artifact preview providers registered through the existing preview registry. Use browser-native lightweight viewers as the implementation path: `rhwp` for HWP/HWPX, `docx-preview` for DOCX, SheetJS CE plus Moldy table UI for XLSX, and `pptxviewjs` for PPTX. Do not add LibreOffice/PDF server conversion in this release.

**Tech Stack:** FastAPI, SQLAlchemy async, LangChain/LangGraph/deepagents, `execute_in_skill`, Marketplace system seeds, Node 22, CommonJS skill scripts, `docx`, SheetJS CE, `pptxgenjs`, Next.js 16, React 19, Tailwind v4, shadcn/ui, `@rhwp/core`, `docx-preview`, SheetJS CE, `pptxviewjs`, Playwright.

## Implementation Status

Status as of 2026-06-07: implemented in branch `codex/document-skills-artifact-viewers` in worktree `/Users/chester/.config/superpowers/worktrees/natural-mold/document-skills-artifact-viewers`.

Final E2E proof was run with real FastAPI and Next.js dev servers:

```bash
cd frontend
E2E_FRONTEND_PORT=3020 E2E_BACKEND_PORT=8020 E2E_WORKERS=1 E2E_TEST_TIMEOUT_MS=240000 pnpm test:e2e -- document-artifact-viewers.spec.ts
```

Result: `1 passed`.

Final screenshots were saved and verified as 1280 x 720 PNG files:

- `output/e2e-captures/20260607-document-artifacts/docx-viewer.png`
- `output/e2e-captures/20260607-document-artifacts/xlsx-viewer.png`
- `output/e2e-captures/20260607-document-artifacts/pptx-viewer.png`
- `output/e2e-captures/20260607-document-artifacts/hwpx-viewer.png`

Each screenshot was opened with `view_image` and showed rendered viewer content for the generated document artifact.

---

## 0. Completion Target

This plan is a development-completion plan, not a design-only handoff. The work is complete only when all of the following are true:

- JS execution is implemented in `execute_in_skill` and covered by backend tests.
- Four built-in marketplace skill packages are implemented, seeded, installable, and covered by backend seed tests.
- The four skills generate real `.docx`, `.xlsx`, `.pptx`, and `.hwpx` files through the normal chat runtime and `execute_in_skill`; direct artifact insertion or direct API fixture insertion does not satisfy completion.
- Generated files are ingested into `conversation_artifacts` and appear in the chat artifact rail.
- Four artifact viewers are implemented and registered in the existing preview registry:
  - HWP/HWPX through `@rhwp/core`
  - DOCX through `docx-preview`
  - XLSX through SheetJS CE table UI
  - PPTX through `pptxviewjs`
- Playwright starts real local FastAPI and Next.js dev servers, creates or reuses the E2E user, installs the four marketplace skills into an E2E agent, sends chat messages that cause the skills to run, approves `execute_in_skill`, opens each artifact viewer, and captures one screenshot per viewer.
- Screenshot files are saved under `output/e2e-captures/<YYYYMMDD>-document-artifacts/`, verified with `file`, and visually inspected with `view_image` before final reporting.
- The final implementation report includes the exact commands run, test results, screenshot paths, and any remaining viewer fidelity limits.

## 1. Current Code Findings

- Repository state before planning: branch `main`, HEAD `b463807`, equal to `origin/main`, clean working tree.
- `backend/app/agent_runtime/skill_executor.py` currently parses tool commands with `shlex.split`, does not spawn a shell, supports narrow env-var expansion, and allows only `python` and `curl`.
- `execute_in_skill` already resolves the requested final path segment as a skill slug and rejects any skill not attached to the agent through `ctx.descriptors`.
- `execute_in_skill` sets `SKILL_OUTPUT_DIR` and `OUTPUTS_DIR` to the conversation output directory. `ArtifactDeltaRecorder` ingests files created by `execute_in_skill` automatically because `backend/app/services/artifact_service.py` lists `execute_in_skill` in `ARTIFACT_SOURCE_TOOL_NAMES`.
- Marketplace built-ins are currently seeded by `backend/app/seed/default_marketplace_skills.py` from `backend/app/seed/system_skill_packages`. Existing built-ins are `image-generation` and `deep-research`.
- Existing artifact classification is in `backend/app/services/artifact_paths.py`. HWP/HWPX are not yet classified. DOCX/PPTX are `document`; XLSX is currently `data`.
- Frontend artifact previews are registered in `frontend/src/components/chat/artifacts/preview-registry.tsx`. Non-text binary previews can set `requiresText: false` and fetch `artifact.preview_url` directly.
- Current PDF provider is a simple iframe in `frontend/src/components/chat/artifacts/providers/pdf-preview.tsx`. New Office viewers should follow the same provider registration shape, not a parallel artifact system.
- Chat approval UI for risky tool calls is already present. `execute_in_skill` has `CODE_EXECUTION` risk and normal chat E2E must approve the card button labeled by `chat.approval.approve`.
- Playwright config already starts real dev servers from `frontend/playwright.config.ts`. It boots backend on `E2E_BACKEND_PORT` and frontend on `E2E_FRONTEND_PORT`.

## 2. Licensing And Dependency Decisions

- Do not copy Anthropic Claude skill package files from `/Users/chester/.claude/plugins/marketplaces/anthropic-agent-skills/skills`. Their license file is restrictive and unsuitable for direct inclusion in Moldy.
- Use the Claude DOCX/XLSX/PPTX skills only as a high-level feature reference. Implement new Moldy-owned package contents and scripts.
- The local Korean patent HWPX skill source at `/Users/chester/dev/claude_prj/ranian963-skills/plugins/patent-hwpx-generator/skills/patent-hwpx-generator` is MIT according to its repo README. It can be adapted, but private team and organization details in the script/references must be removed before adding it as a Moldy built-in.
- `@rhwp/core@0.7.15` is MIT and supports HWP/HWPX parsing/rendering through Rust/WASM. Local source also exists at `/Users/chester/dev/claude_prj/rhwp`.
- `docx-preview@0.3.7` is Apache-2.0 and renders DOCX into HTML in the browser.
- SheetJS CE should be installed from the official SheetJS CDN tarball rather than the stale public npm registry package. Use `xlsx@https://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz` unless the official docs publish a newer CE tarball during implementation.
- `pptxviewjs@1.1.9` is MIT and renders PPTX client-side with Canvas. PPTX is the highest-risk viewer. The E2E-generated PPTX must be checked visually before closing the task.
- `@docmentis/udoc-viewer` is not included in this release. It is attractive as a universal viewer, but its license structure distinguishes the MIT viewer layer from the bundled WASM engine. The planned implementation stays on format-specific lightweight viewers.

## 3. End State

After implementation:

- A selected marketplace skill can run:
  - `python scripts/...`
  - `node scripts/...`
  - `curl ...`
- `node -e`, `node --eval`, `npm`, `npx`, shell pipelines, shell redirects, command chaining, and script paths outside the selected skill directory are rejected.
- Four new built-in marketplace skills are visible as system-seeded items:
  - `docx-document`
  - `xlsx-spreadsheet`
  - `pptx-presentation`
  - `patent-hwpx-generator`
- Installing those built-ins into an agent and chatting with that agent can create:
  - `.docx`
  - `.xlsx`
  - `.pptx`
  - `.hwpx`
- Generated files are ingested into `conversation_artifacts` and appear in the chat artifact rail.
- Artifact preview provider selection resolves to:
  - `hwp-hwpx` for `hwp` and `hwpx`
  - `docx` for `docx`
  - `xlsx` for `xlsx`
  - `pptx` for `pptx`
- Playwright E2E creates all four files through the real runtime, opens each viewer, captures screenshots under `output/e2e-captures/<YYYYMMDD>-document-artifacts/`, verifies each screenshot is a valid PNG with visible content, and the implementation is not marked complete until that evidence exists.

## 4. Implementation Tasks

### Task 1 - Add JS Runner Configuration

- [ ] Edit `backend/app/config.py`.
- [ ] Add settings near the existing skill settings:

```python
    # Skills (JavaScript package runner)
    skill_node_binary: str = "node"
    skill_node_modules_dir: str = "./skill-node/node_modules"
```

- [ ] Add these entries to `backend/.env.example`:

```dotenv
# Skill JavaScript runner
SKILL_NODE_BINARY=node
SKILL_NODE_MODULES_DIR=./skill-node/node_modules
```

- [ ] Keep defaults local-friendly. The backend process usually starts from `backend/`, so `./skill-node/node_modules` resolves to `backend/skill-node/node_modules`.

### Task 2 - Create Shared Node Dependency Package For Skills

- [ ] Add `backend/skill-node/package.json`:

```json
{
  "name": "@moldy/skill-node-runtime",
  "version": "0.1.0",
  "private": true,
  "license": "MIT",
  "type": "commonjs",
  "dependencies": {
    "docx": "9.7.1",
    "pptxgenjs": "4.0.1",
    "xlsx": "https://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz"
  }
}
```

- [ ] Edit root `pnpm-workspace.yaml` and include the package:

```yaml
packages:
  - 'frontend'
  - 'backend/skill-node'
ignoredBuiltDependencies:
  - sharp
  - unrs-resolver
```

- [ ] Run from the repo root:

```bash
pnpm install
```

- [ ] Confirm `backend/skill-node/node_modules` exists after install.
- [ ] Add a short note to `docs/ARCHITECTURE.md` or `docs/PRD.md` only if those docs already describe skill execution dependencies. Keep this plan as the detailed source of truth.

### Task 3 - Extend `execute_in_skill` To Allow Safe Node Scripts

- [ ] Edit `backend/app/agent_runtime/skill_executor.py`.
- [ ] Import `shutil` and `settings`:

```python
import shutil

from app.config import settings
```

- [ ] Add helper functions:

```python
def _resolve_node_binary() -> str | None:
    configured = settings.skill_node_binary.strip() or "node"
    if Path(configured).is_absolute():
        return configured if Path(configured).exists() else None
    return shutil.which(configured, path=os.environ.get("PATH"))


def _resolve_skill_node_modules() -> Path | None:
    raw = settings.skill_node_modules_dir.strip()
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    return path if path.is_dir() else None
```

- [ ] Add node command validation in `_prepare_skill_subprocess_args` after the python branch and before curl:

```python
    if executable == "node":
        if len(args) < 2 or args[1].startswith("-"):
            return None, "Error: node command must be `node scripts/<file>.cjs ...`."
        script_path = (resolved / args[1]).resolve()
        if not script_path.is_relative_to(resolved):
            return None, "Error: node script must be within the skill directory."
        if script_path.suffix.lower() not in {".js", ".cjs", ".mjs"}:
            return None, "Error: node script must use .js, .cjs, or .mjs."
        node_binary = _resolve_node_binary()
        if node_binary is None:
            return None, "Error: node executable is not available for skill execution."
        args[0] = node_binary
        return args, None
```

- [ ] Update the empty command and final error text:

```python
    if not args:
        return None, "Error: command must start with python, node, or curl."
```

```python
    return None, "Error: only python, node, or curl commands are allowed."
```

- [ ] In `execute_in_skill`, add Node-related env values after `OUTPUTS_DIR`:

```python
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin"),
            "PYTHONPATH": str(resolved),
            "HOME": str(resolved),
            "SKILL_OUTPUT_DIR": out,
            "OUTPUTS_DIR": out,
        }
        node_modules = _resolve_skill_node_modules()
        if node_modules is not None:
            env["NODE_PATH"] = str(node_modules)
```

- [ ] Preserve all existing credential injection and redaction logic.
- [ ] Do not enable shell execution. Keep `asyncio.create_subprocess_exec`.

### Task 4 - Add Skill Executor Unit Tests

- [ ] Add tests to the existing backend test file that covers `skill_executor.py`, or create `backend/tests/test_skill_executor_node.py`.
- [ ] Test accepted command:

```python
args, error = _prepare_skill_subprocess_args(
    "node scripts/create_docx.cjs --sample",
    resolved=skill_dir,
    env={"OUTPUTS_DIR": str(output_dir)},
)
assert error is None
assert args is not None
assert args[1] == "scripts/create_docx.cjs"
```

- [ ] Test rejected commands:
  - `node -e "console.log(1)"`
  - `node --eval "console.log(1)"`
  - `node ../escape.cjs`
  - `npm run build`
  - `npx anything`
  - `node scripts/create_docx.txt`
- [ ] Test `$OUTPUTS_DIR` expansion still works for node args:

```python
args, error = _prepare_skill_subprocess_args(
    "node scripts/create_docx.cjs --input $OUTPUTS_DIR/docx-spec.json",
    resolved=skill_dir,
    env={"OUTPUTS_DIR": str(output_dir)},
)
assert error is None
assert str(output_dir / "docx-spec.json") in args
```

- [ ] Run:

```bash
cd backend
uv run pytest tests/test_skill_executor_node.py
```

### Task 5 - Refactor Built-In Marketplace Skill Seeding

- [ ] Edit `backend/app/seed/default_marketplace_skills.py`.
- [ ] Introduce a dataclass so six system skills can be seeded by one path:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class SystemSkillSeedSpec:
    slug: str
    name: str
    description: str
    skill_dir: Path
    categories: list[str]
    tags: list[str]
    locale: str
    version_label: str
    payload: dict[str, Any]
    credential_requirements: list[dict[str, Any]]
    execution_profile: dict[str, Any]
    release_notes: str
```

- [ ] Replace the duplicated seed functions with `_seed_system_skill(db, spec, storage_root)`.
- [ ] Preserve existing behavior:
  - scan package with `scan_package`
  - use content hash to avoid duplicate versions
  - `source_kind="system_seed"`
  - `source_external_id=spec.slug`
  - `visibility="system"`
  - `status="published"`
  - storage under `marketplace/system-skills/<version_id>`
  - `latest_version_id` points to existing matching version when content hash is unchanged
- [ ] Keep existing `image-generation` and `deep-research` payloads byte-for-byte equivalent where possible so existing tests still express the same contract.
- [ ] Add the four document specs after the existing two specs.

### Task 6 - Add DOCX Built-In Skill Package

- [ ] Create:

```text
backend/app/seed/system_skill_packages/docx-document/
├── SKILL.md
├── examples/
│   └── e2e-docx.json
└── scripts/
    └── create_docx.cjs
```

- [ ] `SKILL.md` must instruct the model to:
  - create or choose a JSON spec
  - run `execute_in_skill` with `node scripts/create_docx.cjs`
  - write outputs only under `$OUTPUTS_DIR`
  - return the generated file name to the user
- [ ] Use this command example in `SKILL.md`:

```text
node scripts/create_docx.cjs --input examples/e2e-docx.json --output moldy-docx-demo.docx
```

- [ ] `examples/e2e-docx.json` should contain a small Korean report with:
  - title
  - subtitle
  - two sections
  - one bullet list
  - one simple table
- [ ] `scripts/create_docx.cjs` must:
  - use CommonJS `require`
  - require `docx`
  - parse `--input <path>` and `--output <filename>`
  - allow input paths only inside the skill directory or `$OUTPUTS_DIR`
  - allow output paths only inside `$OUTPUTS_DIR`
  - generate deterministic metadata and body content
  - write a `.docx` file with `Packer.toBuffer`
  - print a JSON line such as `{"ok":true,"file":"moldy-docx-demo.docx"}`
- [ ] Use a small helper in the script:

```javascript
function resolveInside(baseDir, candidate, label) {
  const resolved = path.resolve(baseDir, candidate);
  const root = path.resolve(baseDir);
  if (resolved !== root && !resolved.startsWith(root + path.sep)) {
    throw new Error(`${label} must stay inside ${root}`);
  }
  return resolved;
}
```

- [ ] The script may use `process.env.OUTPUTS_DIR` and `process.cwd()` only. It must not read arbitrary filesystem paths.

### Task 7 - Add XLSX Built-In Skill Package

- [ ] Create:

```text
backend/app/seed/system_skill_packages/xlsx-spreadsheet/
├── SKILL.md
├── examples/
│   └── e2e-xlsx.json
└── scripts/
    └── create_xlsx.cjs
```

- [ ] `SKILL.md` command example:

```text
node scripts/create_xlsx.cjs --input examples/e2e-xlsx.json --output moldy-xlsx-demo.xlsx
```

- [ ] `examples/e2e-xlsx.json` should contain:
  - workbook title
  - at least two sheets
  - headers and rows
  - basic number and date values
- [ ] `scripts/create_xlsx.cjs` must:
  - require `xlsx`
  - parse the JSON spec
  - create a workbook with `XLSX.utils.book_new`
  - create worksheets with `XLSX.utils.aoa_to_sheet`
  - set column widths where useful
  - write with `XLSX.writeFile`
  - force output under `$OUTPUTS_DIR`
  - print the generated file name
- [ ] Keep formula usage minimal for the first release because the browser viewer displays formula strings or cached values depending on workbook state. The E2E sample should use concrete values.

### Task 8 - Add PPTX Built-In Skill Package

- [ ] Create:

```text
backend/app/seed/system_skill_packages/pptx-presentation/
├── SKILL.md
├── examples/
│   └── e2e-pptx.json
└── scripts/
    └── create_pptx.cjs
```

- [ ] `SKILL.md` command example:

```text
node scripts/create_pptx.cjs --input examples/e2e-pptx.json --output moldy-pptx-demo.pptx
```

- [ ] `examples/e2e-pptx.json` should contain:
  - deck title
  - three slides
  - one chart-like list or metric slide
  - one closing slide
- [ ] `scripts/create_pptx.cjs` must:
  - require `pptxgenjs`
  - avoid rare PPTX constructs for the first release
  - use text boxes, rectangles, simple fills, simple lines, and embedded tables only
  - set `pptx.layout = "LAYOUT_WIDE"`
  - write with `pptx.writeFile({ fileName })`
  - force output under `$OUTPUTS_DIR`
  - print the generated file name
- [ ] Keep the generated PPTX intentionally simple because the required viewer path is `pptxviewjs`.

### Task 9 - Add Korean Patent HWPX Built-In Skill Package

- [ ] Create:

```text
backend/app/seed/system_skill_packages/patent-hwpx-generator/
├── SKILL.md
├── assets/
│   └── template.hwpx
├── examples/
│   └── e2e-patent.json
├── references/
│   ├── hwpx-xml-guide.md
│   └── patent-writing-guide.md
└── scripts/
    └── generate_hwpx.py
```

- [ ] Adapt from the MIT source under `/Users/chester/dev/claude_prj/ranian963-skills/plugins/patent-hwpx-generator/skills/patent-hwpx-generator`.
- [ ] Remove all private names, team info, organization info, and personal contact details from copied references and script defaults.
- [ ] Keep `assets/template.hwpx` only after confirming it contains no private text:

```bash
python - <<'PY'
import zipfile
from pathlib import Path
p = Path("backend/app/seed/system_skill_packages/patent-hwpx-generator/assets/template.hwpx")
with zipfile.ZipFile(p) as z:
    for name in z.namelist():
        if name.endswith(".xml"):
            text = z.read(name).decode("utf-8", errors="ignore")
            for needle in ["유 수 화", "Ranian", "ranian", "개인", "전화", "이메일"]:
                if needle in text:
                    raise SystemExit(f"private marker found: {needle} in {name}")
print("template scan passed")
PY
```

- [ ] `SKILL.md` command example must use `python`, not `python3`:

```text
python scripts/generate_hwpx.py --input examples/e2e-patent.json --output moldy-patent-demo.hwpx
```

- [ ] `generate_hwpx.py` must:
  - use Python stdlib only
  - parse `--input` and `--output`
  - allow input paths only inside the skill directory or `$OUTPUTS_DIR`
  - allow output paths only inside `$OUTPUTS_DIR`
  - preserve HWPX ZIP metadata where the existing script already does so
  - update only the XML parts required for visible content
  - print the generated file name
- [ ] `examples/e2e-patent.json` must contain a harmless sample invention:
  - title: `AI 에이전트 문서 생성 결과 검증 방법`
  - abstract
  - background
  - claims
  - inventor names as generic Korean names

### Task 10 - Define System Skill Specs

- [ ] In `backend/app/seed/default_marketplace_skills.py`, add execution profiles:

```python
JS_DOCUMENT_EXECUTION_PROFILE: dict[str, Any] = {
    "support_level": "node_package",
    "runners": ["node"],
    "requires_node": True,
    "timeout_seconds": 120,
}

PATENT_HWPX_EXECUTION_PROFILE: dict[str, Any] = {
    "support_level": "ready_python",
    "runners": ["python"],
    "requires_python": True,
    "timeout_seconds": 120,
}
```

- [ ] Add specs:
  - `docx-document`: categories `["document"]`, tags `["docx", "office", "document"]`
  - `xlsx-spreadsheet`: categories `["document", "data"]`, tags `["xlsx", "office", "spreadsheet"]`
  - `pptx-presentation`: categories `["document"]`, tags `["pptx", "office", "presentation"]`
  - `patent-hwpx-generator`: categories `["document", "patent"]`, tags `["hwpx", "patent", "korean"]`
- [ ] Payload shape for document skills:

```python
{
    "kind": "package",
    "name": spec.slug,
    "version": "0.1.0",
    "runtime": "node",
    "artifact_extensions": ["docx"],
}
```

- [ ] Payload shape for HWPX:

```python
{
    "kind": "package",
    "name": "patent-hwpx-generator",
    "version": "0.1.0",
    "runtime": "python",
    "artifact_extensions": ["hwpx"],
}
```

- [ ] All four document skills have no credential requirements.

### Task 11 - Add Backend Seed Tests

- [ ] Extend `backend/tests/test_default_image_skill_seed.py` or rename to `backend/tests/test_default_marketplace_skill_seed.py`.
- [ ] Keep the existing image and deep research tests passing.
- [ ] Add a parametrized test over the four new slugs:

```python
@pytest.mark.parametrize(
    ("slug", "runner", "extension"),
    [
        ("docx-document", "node", "docx"),
        ("xlsx-spreadsheet", "node", "xlsx"),
        ("pptx-presentation", "node", "pptx"),
        ("patent-hwpx-generator", "python", "hwpx"),
    ],
)
async def test_seed_default_document_skills(...):
    ...
```

- [ ] Assert:
  - `item.is_system is True`
  - `item.visibility == "system"`
  - `item.status == "published"`
  - `version.payload["name"] == slug`
  - `runner in version.execution_profile["runners"]`
  - `extension in version.payload["artifact_extensions"]`
  - copied `SKILL.md` exists under `resolve_data_path(version.storage_path)`
- [ ] Add one idempotency assertion that seeding twice still creates one item and one version per slug.
- [ ] Run:

```bash
cd backend
uv run pytest tests/test_default_marketplace_skill_seed.py
```

### Task 12 - Improve Artifact Classification For HWP/HWPX

- [ ] Edit `backend/app/services/artifact_paths.py`.
- [ ] Add custom MIME lookup:

```python
_CUSTOM_MIME_TYPES = {
    "hwp": "application/x-hwp",
    "hwpx": "application/x-hwpx",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
```

- [ ] Use it before `mimetypes.guess_type`:

```python
mime_type = _CUSTOM_MIME_TYPES.get(extension or "")
if mime_type is None:
    mime_type = mimetypes.guess_type(relative.name)[0] or "application/octet-stream"
```

- [ ] Update `artifact_kind_for`:

```python
    if ext in {"doc", "docx", "ppt", "pptx", "hwp", "hwpx"}:
        return "document"
```

- [ ] Keep `xlsx` as `data` unless product wants all Office files grouped as `document`. The frontend preview provider will match `xlsx` by extension either way.
- [ ] Add tests in the artifact paths test file or create `backend/tests/test_artifact_paths_document_types.py`.
- [ ] Assert `hwpx` and `hwp` classify as `document` and get the custom MIME values.

### Task 13 - Add Frontend Viewer Dependencies

- [ ] Run:

```bash
cd frontend
pnpm add @rhwp/core@0.7.15 docx-preview@0.3.7 pptxviewjs@1.1.9 chart.js@^4.4.1 xlsx@https://cdn.sheetjs.com/xlsx-0.20.3/xlsx-0.20.3.tgz
```

- [ ] If TypeScript lacks module declarations for `pptxviewjs`, add `frontend/src/types/pptxviewjs.d.ts`:

```typescript
declare module 'pptxviewjs' {
  export interface PPTXViewerOptions {
    canvas: HTMLCanvasElement
    autoExposeGlobals?: boolean
  }

  export class PPTXViewer {
    constructor(options: PPTXViewerOptions)
    loadFile(file: File | ArrayBuffer | Uint8Array | Blob): Promise<void>
    loadFromUrl(url: string): Promise<void>
    render(canvas?: HTMLCanvasElement): Promise<void>
    renderSlide(index: number, canvas?: HTMLCanvasElement): Promise<void>
    nextSlide(canvas?: HTMLCanvasElement): Promise<void>
    previousSlide(canvas?: HTMLCanvasElement): Promise<void>
    on(eventName: string, callback: (payload: unknown) => void): void
  }
}
```

- [ ] If TypeScript lacks `@rhwp/core` declarations, add a narrow declaration file based on local `rhwp/typescript/rhwp.d.ts`.

### Task 14 - Copy rhwp WASM Asset Into Public

- [ ] Add `frontend/scripts/copy-rhwp-wasm.mjs`:

```javascript
import { copyFile, mkdir } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..')
const source = join(root, 'node_modules', '@rhwp', 'core', 'rhwp_bg.wasm')
const targetDir = join(root, 'public', 'vendor', 'rhwp')
const target = join(targetDir, 'rhwp_bg.wasm')

await mkdir(targetDir, { recursive: true })
await copyFile(source, target)
console.log(`copied ${source} -> ${target}`)
```

- [ ] Edit `frontend/package.json` scripts:

```json
{
  "scripts": {
    "prepare:assets": "node scripts/copy-rhwp-wasm.mjs",
    "dev": "pnpm prepare:assets && next dev",
    "build": "pnpm prepare:assets && next build --webpack"
  }
}
```

- [ ] Keep all existing scripts not shown above unchanged.
- [ ] Run:

```bash
cd frontend
pnpm prepare:assets
file public/vendor/rhwp/rhwp_bg.wasm
```

### Task 15 - Add Shared Binary Fetch Helper

- [ ] Add `frontend/src/components/chat/artifacts/providers/use-artifact-binary.ts`:

```typescript
import { useQuery } from '@tanstack/react-query'
import { resolveImageUrl } from '@/lib/utils'
import type { ArtifactSummary } from '@/lib/types'

export function useArtifactArrayBuffer(artifact: ArtifactSummary) {
  return useQuery({
    queryKey: ['artifact-binary', artifact.id, artifact.version_id],
    queryFn: async () => {
      const url = resolveImageUrl(artifact.preview_url) ?? artifact.preview_url
      const response = await fetch(url, { credentials: 'include' })
      if (!response.ok) throw new Error(`Failed to fetch artifact: ${response.status}`)
      return response.arrayBuffer()
    },
    staleTime: 30_000,
  })
}
```

- [ ] Reuse this in HWP/HWPX, DOCX, XLSX, and PPTX providers.

### Task 16 - Add Shared Document Preview Shell

- [ ] Add `frontend/src/components/chat/artifacts/providers/document-preview-shell.tsx`.
- [ ] The component should accept:
  - `title`
  - `isLoading`
  - `error`
  - `children`
  - optional toolbar content
- [ ] Use existing Moldy classes only:
  - `moldy-muted-panel`
  - `border-border`
  - `bg-background`
  - `text-muted-foreground`
- [ ] Do not use restricted classes such as `rounded-xl`, raw hex colors, or `transition-all`.
- [ ] Add i18n messages under `chat.rightRail.artifacts.documentPreview`:

Korean:

```json
{
  "loading": "문서를 불러오는 중입니다",
  "errorTitle": "미리보기를 열 수 없습니다",
  "downloadInstead": "다운로드",
  "page": "{current} / {total}",
  "zoomIn": "확대",
  "zoomOut": "축소",
  "previousPage": "이전 페이지",
  "nextPage": "다음 페이지",
  "sheet": "시트",
  "slide": "슬라이드"
}
```

English:

```json
{
  "loading": "Loading document",
  "errorTitle": "Preview unavailable",
  "downloadInstead": "Download",
  "page": "{current} / {total}",
  "zoomIn": "Zoom in",
  "zoomOut": "Zoom out",
  "previousPage": "Previous page",
  "nextPage": "Next page",
  "sheet": "Sheet",
  "slide": "Slide"
}
```

### Task 17 - Add HWP/HWPX Preview Provider With rhwp

- [ ] Add `frontend/src/components/chat/artifacts/providers/hwp-preview.tsx`.
- [ ] Implement lazy WASM init:

```typescript
let rhwpInitPromise: Promise<typeof import('@rhwp/core')> | null = null

async function loadRhwp() {
  if (!rhwpInitPromise) {
    rhwpInitPromise = import('@rhwp/core').then(async (mod) => {
      ensureMeasureTextWidth()
      await mod.default({ module_or_path: '/vendor/rhwp/rhwp_bg.wasm' })
      return mod
    })
  }
  return rhwpInitPromise
}
```

- [ ] Implement `ensureMeasureTextWidth()` with a canvas:

```typescript
function ensureMeasureTextWidth() {
  const target = globalThis as typeof globalThis & {
    measureTextWidth?: (font: string, text: string) => number
  }
  if (target.measureTextWidth) return
  const canvas = document.createElement('canvas')
  const context = canvas.getContext('2d')
  target.measureTextWidth = (font, text) => {
    if (!context) return text.length * 10
    context.font = font
    return context.measureText(text).width
  }
}
```

- [ ] Use `new HwpDocument(new Uint8Array(buffer))`.
- [ ] Render each page with `doc.renderPageSvg(pageIndex)`.
- [ ] Convert each SVG string to a Blob URL and render it through `<img>`. Do not use `dangerouslySetInnerHTML`.
- [ ] Track page index and zoom. Render one page at a time for performance.
- [ ] On cleanup, revoke Blob URLs and call `doc.free()` if present.
- [ ] Export provider:

```typescript
export const HwpPreviewProvider: ArtifactPreviewProvider = {
  id: 'hwp-hwpx',
  priority: 89,
  requiresText: false,
  extensions: ['hwp', 'hwpx'],
  mimeTypes: ['application/x-hwp', 'application/x-hwpx', 'application/hwp+zip'],
  render: (props) => <HwpPreview {...props} />,
}
```

### Task 18 - Add DOCX Preview Provider

- [ ] Add `frontend/src/components/chat/artifacts/providers/docx-preview.tsx`.
- [ ] Fetch artifact as `ArrayBuffer` through `useArtifactArrayBuffer`.
- [ ] Use dynamic import:

```typescript
const docx = await import('docx-preview')
await docx.renderAsync(buffer, container, styleContainer, {
  className: 'moldy-docx',
  inWrapper: true,
  ignoreFonts: false,
  ignoreWidth: false,
  ignoreHeight: false,
  useBase64URL: true,
})
```

- [ ] Render into a ref-owned container and clear it before each render.
- [ ] Use `DocumentPreviewShell` for loading/error toolbar.
- [ ] Add CSS in `frontend/src/app/globals.css` using scoped `.moldy-docx` selectors only. Keep page wrapper width responsive:

```css
.moldy-docx-wrapper {
  max-width: 100%;
  overflow: auto;
}
```

- [ ] Export provider:

```typescript
export const DocxPreviewProvider: ArtifactPreviewProvider = {
  id: 'docx',
  priority: 88,
  requiresText: false,
  extensions: ['docx'],
  mimeTypes: ['application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
  render: (props) => <DocxPreview {...props} />,
}
```

### Task 19 - Add XLSX Preview Provider

- [ ] Add `frontend/src/components/chat/artifacts/providers/xlsx-preview.tsx`.
- [ ] Fetch artifact as `ArrayBuffer`.
- [ ] Dynamically import SheetJS:

```typescript
const XLSX = await import('xlsx')
const workbook = XLSX.read(buffer, { type: 'array', cellDates: true })
```

- [ ] Keep viewer behavior focused:
  - show sheet tabs
  - render the active sheet as an HTML table
  - use `XLSX.utils.sheet_to_json(sheet, { header: 1, raw: false, blankrows: false })`
  - cap initial display to 200 rows and 50 columns
  - show a localized note when the cap is applied
- [ ] Render all cell values as React text, never as HTML.
- [ ] Add i18n messages for row/column cap:

Korean:

```json
{
  "truncatedGrid": "큰 시트라 처음 {rows}행과 {columns}열만 표시합니다"
}
```

English:

```json
{
  "truncatedGrid": "Large sheet preview shows the first {rows} rows and {columns} columns"
}
```

- [ ] Export provider:

```typescript
export const XlsxPreviewProvider: ArtifactPreviewProvider = {
  id: 'xlsx',
  priority: 87,
  requiresText: false,
  extensions: ['xlsx', 'xls'],
  mimeTypes: [
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-excel',
  ],
  render: (props) => <XlsxPreview {...props} />,
}
```

### Task 20 - Add PPTX Preview Provider

- [ ] Add `frontend/src/components/chat/artifacts/providers/pptx-preview.tsx`.
- [ ] Fetch artifact as `ArrayBuffer`.
- [ ] Use a canvas:

```typescript
const canvasRef = useRef<HTMLCanvasElement | null>(null)
```

- [ ] Dynamically import `pptxviewjs`:

```typescript
const { PPTXViewer } = await import('pptxviewjs')
const viewer = new PPTXViewer({ canvas, autoExposeGlobals: true })
await viewer.loadFile(buffer)
await viewer.render(canvas)
```

- [ ] Track slide count from `loadComplete` if the event exposes it. If the event payload shape is weaker than docs, keep navigation usable by trying `nextSlide`/`previousSlide` and storing the current slide index locally.
- [ ] Keep a stable 16:9 canvas container so the layout does not jump.
- [ ] Export provider:

```typescript
export const PptxPreviewProvider: ArtifactPreviewProvider = {
  id: 'pptx',
  priority: 86,
  requiresText: false,
  extensions: ['pptx'],
  mimeTypes: ['application/vnd.openxmlformats-officedocument.presentationml.presentation'],
  render: (props) => <PptxPreview {...props} />,
}
```

- [ ] If `pptxviewjs` cannot render the generated sample deck in Playwright, simplify the generated `pptx-presentation` sample to text, rectangles, and basic shapes, then fix the provider until the generated deck renders in the client. Do not add server PDF conversion.

### Task 21 - Register New Providers

- [ ] Edit `frontend/src/components/chat/artifacts/preview-registry.tsx`.
- [ ] Add lazy imports:

```typescript
const HwpPreview = lazy(() =>
  import('./providers/hwp-preview').then((m) => ({ default: m.HwpPreview })),
)
const DocxPreview = lazy(() =>
  import('./providers/docx-preview').then((m) => ({ default: m.DocxPreview })),
)
const XlsxPreview = lazy(() =>
  import('./providers/xlsx-preview').then((m) => ({ default: m.XlsxPreview })),
)
const PptxPreview = lazy(() =>
  import('./providers/pptx-preview').then((m) => ({ default: m.PptxPreview })),
)
```

- [ ] Register providers before the generic code, text, and unsupported-file providers.
- [ ] Keep provider priority lower than PDF `90` and higher than markdown/data/code providers. Use:
  - HWP/HWPX `89`
  - DOCX `88`
  - XLSX `87`
  - PPTX `86`
- [ ] Add matching provider objects inline or import exported provider constants. Prefer exported constants for unit tests.

### Task 22 - Add Frontend Unit Tests

- [ ] Extend `frontend/src/components/chat/artifacts/__tests__/preview-registry.test.tsx`.
- [ ] Add assertions:

```typescript
expect(getArtifactPreviewProvider(artifact({ extension: 'hwpx', mime_type: 'application/x-hwpx', artifact_kind: 'document' })).id).toBe('hwp-hwpx')
expect(getArtifactPreviewProvider(artifact({ extension: 'docx', mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', artifact_kind: 'document' })).id).toBe('docx')
expect(getArtifactPreviewProvider(artifact({ extension: 'xlsx', mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', artifact_kind: 'data' })).id).toBe('xlsx')
expect(getArtifactPreviewProvider(artifact({ extension: 'pptx', mime_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation', artifact_kind: 'document' })).id).toBe('pptx')
```

- [ ] Add focused component tests where practical:
  - XLSX viewer renders a mocked workbook table from a small binary fixture or a mocked `xlsx` import.
  - DOCX viewer calls `renderAsync` with fetched ArrayBuffer.
  - HWP viewer calls `renderPageSvg` and renders an `<img>`.
  - PPTX viewer calls `loadFile` and `render`.
- [ ] Mock browser APIs that jsdom lacks:
  - canvas context
  - Blob URL creation
  - dynamic viewer modules
- [ ] Run:

```bash
cd frontend
pnpm test --run src/components/chat/artifacts/__tests__/preview-registry.test.tsx src/components/chat/artifacts/__tests__/artifact-preview.test.tsx
pnpm lint:i18n
pnpm lint:design-system
```

### Task 23 - Add E2E Scripted Model For Deterministic Runtime Tests

- [ ] Add settings to `backend/app/config.py`:

```python
    e2e_scripted_model_enabled: bool = False
```

- [ ] Add to `backend/.env.example`:

```dotenv
E2E_SCRIPTED_MODEL_ENABLED=false
```

- [ ] Add `backend/app/agent_runtime/e2e_scripted_model.py`.
- [ ] Only use this provider when `settings.e2e_scripted_model_enabled` is true and `settings.app_env != "production"`.
- [ ] Implement a minimal `BaseChatModel` subclass that:
  - supports `bind_tools` by storing tool names and returning `self`
  - inspects the latest human message
  - emits one `AIMessage` tool call to `execute_in_skill`
  - returns a final assistant message after a `ToolMessage`
- [ ] Tool-call mapping:

```python
SCRIPTED_DOCUMENT_COMMANDS = {
    "E2E_DOCX": {
        "skill_directory": "/skills/docx-document",
        "command": "node scripts/create_docx.cjs --input examples/e2e-docx.json --output moldy-docx-demo.docx",
    },
    "E2E_XLSX": {
        "skill_directory": "/skills/xlsx-spreadsheet",
        "command": "node scripts/create_xlsx.cjs --input examples/e2e-xlsx.json --output moldy-xlsx-demo.xlsx",
    },
    "E2E_PPTX": {
        "skill_directory": "/skills/pptx-presentation",
        "command": "node scripts/create_pptx.cjs --input examples/e2e-pptx.json --output moldy-pptx-demo.pptx",
    },
    "E2E_HWPX": {
        "skill_directory": "/skills/patent-hwpx-generator",
        "command": "python scripts/generate_hwpx.py --input examples/e2e-patent.json --output moldy-patent-demo.hwpx",
    },
}
```

- [ ] Patch `backend/app/agent_runtime/model_factory.py`:

```python
if settings.e2e_scripted_model_enabled and settings.app_env != "production":
    from app.agent_runtime.e2e_scripted_model import E2EScriptedChatModel

    PROVIDER_MAP["e2e_scripted"] = E2EScriptedChatModel
```

- [ ] Ensure production boot cannot enable it:

```python
if settings.e2e_scripted_model_enabled and settings.app_env == "production":
    raise RuntimeError("E2E scripted model cannot run in production")
```

Add this guard in app startup or the scripted model module import path.

### Task 24 - Seed E2E Scripted Model In Dev

- [ ] Add `backend/app/seed/e2e_scripted_model.py`.
- [ ] When `E2E_SCRIPTED_MODEL_ENABLED=true` and `APP_ENV != "production"`, upsert a model row:
  - provider: `e2e_scripted`
  - model_name: `document-artifact-scripted`
  - display_name: `E2E Scripted Document Model`
  - is_default: false
  - cost fields: zero decimals
- [ ] Call this seed from `backend/app/main.py` during startup near the E2E user seed, guarded by the same setting.
- [ ] Add a backend test that the seed is skipped by default and active only when enabled.

### Task 25 - Add E2E Helpers For Marketplace Skill Install And Agent Creation

- [ ] Add `frontend/e2e/document-artifact-viewers.spec.ts`.
- [ ] Use Playwright request APIs after global auth is established.
- [ ] The spec should:
  - find marketplace items by slug
  - install each item for the E2E user
  - create or update one test agent with all four installed skills attached
  - set the agent model to provider `e2e_scripted`, model `document-artifact-scripted`
  - create a conversation
  - navigate to `/agents/<agentId>/conversations/<conversationId>`
- [ ] Prefer existing frontend API shape where helpers already exist. If API helper code is easier than UI creation, use API for setup and reserve UI for chat/send/approval/preview verification.
- [ ] The spec must exercise real chat runtime, not direct artifact API insertion.

### Task 26 - Add E2E Chat And Approval Flow

- [ ] In `frontend/e2e/document-artifact-viewers.spec.ts`, define:

```typescript
const cases = [
  { marker: 'E2E_DOCX', file: 'moldy-docx-demo.docx', providerText: /DOCX|문서/ },
  { marker: 'E2E_XLSX', file: 'moldy-xlsx-demo.xlsx', providerText: /시트|Sheet/ },
  { marker: 'E2E_PPTX', file: 'moldy-pptx-demo.pptx', providerText: /슬라이드|Slide/ },
  { marker: 'E2E_HWPX', file: 'moldy-patent-demo.hwpx', providerText: /페이지|Page|청구항/ },
]
```

- [ ] For each case:
  - fill `textarea[data-moldy-composer-input="true"]`
  - click send button by role/name from messages
  - wait for approval card text `승인이 필요합니다` or `Approval Required`
  - click `승인` or `Approve`
  - wait for generated file name in the artifact rail or message artifact chip
  - open the artifact preview
  - wait for viewer-specific visible content
  - take screenshot
- [ ] Use screenshot paths:

```text
output/e2e-captures/20260607-document-artifacts/docx-preview.png
output/e2e-captures/20260607-document-artifacts/xlsx-preview.png
output/e2e-captures/20260607-document-artifacts/pptx-preview.png
output/e2e-captures/20260607-document-artifacts/hwpx-preview.png
```

- [ ] The date folder should use the actual execution date if this plan is implemented later than 2026-06-07.
- [ ] After screenshots, verify files from the shell:

```bash
file output/e2e-captures/20260607-document-artifacts/*.png
```

- [ ] Open each image with `view_image` before final reporting when Codex runs the implementation.

### Task 27 - Update Playwright Web Server Env

- [ ] Edit `frontend/playwright.config.ts`.
- [ ] Add E2E scripted model env to the backend server command:

```typescript
command: `cd ../backend && E2E_SCRIPTED_MODEL_ENABLED=true CORS_ALLOWED_ORIGINS=${corsOrigins} uv run uvicorn app.main:app --port ${backendPort}`,
```

- [ ] Keep `reuseExistingServer: true`.
- [ ] Document in the spec header that an already-running backend must also have `E2E_SCRIPTED_MODEL_ENABLED=true`.

### Task 28 - Local Manual Verification Before Full E2E

- [ ] Install dependencies:

```bash
pnpm install
cd frontend && pnpm install
```

- [ ] Confirm Node runner deps:

```bash
cd backend
node -e "require('docx'); require('xlsx'); require('pptxgenjs'); console.log('skill node deps ok')"
```

- [ ] Run generated skill scripts directly with env:

```bash
cd backend/app/seed/system_skill_packages/docx-document
OUTPUTS_DIR=/tmp/moldy-doc-test NODE_PATH=/Users/chester/dev/ref/natural-mold/backend/skill-node/node_modules node scripts/create_docx.cjs --input examples/e2e-docx.json --output moldy-docx-demo.docx
file /tmp/moldy-doc-test/moldy-docx-demo.docx
```

- [ ] Repeat for XLSX, PPTX, and HWPX.
- [ ] Confirm HWPX is a ZIP:

```bash
file /tmp/moldy-doc-test/moldy-patent-demo.hwpx
python -m zipfile -l /tmp/moldy-doc-test/moldy-patent-demo.hwpx | head
```

### Task 29 - Full Verification Commands

- [ ] Backend targeted tests:

```bash
cd backend
uv run pytest tests/test_skill_executor_node.py tests/test_default_document_skill_seed.py tests/test_e2e_scripted_model.py tests/test_artifact_paths.py tests/test_runtime_isolation.py::TestExecuteInSkillPathValidation tests/agent_runtime/test_credential_resolution.py -q
uv run ruff check app/agent_runtime/skill_executor.py app/agent_runtime/e2e_scripted_model.py app/agent_runtime/model_factory.py app/agent_runtime/credential_resolution.py app/seed/default_marketplace_skills.py app/seed/e2e_scripted_model.py app/services/artifact_paths.py tests/test_skill_executor_node.py tests/test_default_document_skill_seed.py tests/test_e2e_scripted_model.py tests/test_artifact_paths.py tests/agent_runtime/test_credential_resolution.py
```

- [ ] Frontend targeted tests:

```bash
cd frontend
pnpm exec eslint src/components/chat/artifacts/providers/docx-preview.tsx src/components/chat/artifacts/providers/pptx-preview.tsx e2e/document-artifact-viewers.spec.ts --no-ignore
pnpm test --run src/components/chat/artifacts/__tests__/preview-registry.test.tsx
pnpm lint:i18n
pnpm lint:design-system
pnpm build
```

- [ ] Full E2E for this feature:

```bash
cd frontend
E2E_FRONTEND_PORT=3020 E2E_BACKEND_PORT=8020 E2E_WORKERS=1 E2E_TEST_TIMEOUT_MS=240000 pnpm test:e2e -- document-artifact-viewers.spec.ts
```

- [ ] If the E2E backend port is already in use by a backend without `E2E_SCRIPTED_MODEL_ENABLED=true`, stop that backend or run with an alternate port pair:

```bash
cd frontend
E2E_BACKEND_PORT=8010 E2E_FRONTEND_PORT=3010 E2E_WORKERS=1 E2E_TEST_TIMEOUT_MS=180000 pnpm test:e2e -- document-artifact-viewers.spec.ts
```

### Task 30 - Acceptance Criteria

- [ ] `execute_in_skill` accepts `node scripts/create_docx.cjs ...` and rejects `node -e`.
- [ ] The four new system skills are seeded exactly once per content hash and can be installed from marketplace.
- [ ] The DOCX skill generates a `.docx` artifact through chat runtime.
- [ ] The XLSX skill generates a `.xlsx` artifact through chat runtime.
- [ ] The PPTX skill generates a `.pptx` artifact through chat runtime.
- [ ] The Korean patent skill generates a `.hwpx` artifact through chat runtime.
- [ ] The artifact rail opens all four files without falling back to the generic unsupported-file card.
- [ ] HWP/HWPX renders through rhwp.
- [ ] DOCX renders through `docx-preview`.
- [ ] XLSX renders through SheetJS table UI.
- [ ] PPTX renders through `pptxviewjs`.
- [ ] Playwright runs against real local backend/frontend servers, not mocked artifact APIs.
- [ ] Playwright triggers actual chat runtime skill execution for all four document types.
- [ ] Playwright approves the `execute_in_skill` code-execution approval card for each generated file.
- [ ] Playwright screenshots are captured and visually inspected:
  - `output/e2e-captures/<YYYYMMDD>-document-artifacts/docx-viewer.png`
  - `output/e2e-captures/<YYYYMMDD>-document-artifacts/xlsx-viewer.png`
  - `output/e2e-captures/<YYYYMMDD>-document-artifacts/pptx-viewer.png`
  - `output/e2e-captures/<YYYYMMDD>-document-artifacts/hwpx-viewer.png`
- [ ] `file output/e2e-captures/<YYYYMMDD>-document-artifacts/*.png` reports valid PNG images.
- [ ] Each screenshot is opened with `view_image` and shows the generated document viewer content, not the unsupported-file card, a blank canvas, or a loading-only state.
- [ ] No proprietary Claude skill package content is copied.
- [ ] No private team/person data from the local patent HWPX source remains in Moldy built-ins.
- [ ] `pnpm lint:i18n` and `pnpm lint:design-system` pass after frontend copy/UI changes.

## 5. No Server Conversion Policy

This release does not include LibreOffice, server-side Office-to-PDF conversion, or a universal document conversion service. The acceptance target is narrower and stricter: the files generated by Moldy's own four built-in document skills must render through the four planned client-side viewers.

- DOCX: if `docx-preview` cannot parse the generated file, fix the DOCX generator or provider until the generated sample renders.
- XLSX: render workbook data through SheetJS table UI. Charts, pivot tables, and formula evaluation are outside the first release surface.
- PPTX: if `pptxviewjs` cannot render the generated file, simplify the generated deck and fix the provider until the generated sample renders.
- HWP/HWPX: if rhwp cannot parse the generated HWPX, inspect and fix the generated package XML. The built-in skill must emit a rhwp-compatible HWPX.

## 6. Notes For Future Hardening

- JS runner is still a local subprocess, not a full sandbox. It is intentionally narrow: selected skill only, no shell, no npm/npx, no inline eval, timeout bounded, output dir controlled, credentials redacted. A later LangChain sandbox service can move dangerous execution out of the backend process.
- Browser document viewers should be treated as previewers, not editors. Editing, tracked changes, macro handling, advanced spreadsheet charts, and complex slide animations are outside this release.
- The generated document skills should keep their examples visually simple and deterministic. The viewer E2E is testing the Moldy integration path, not parity with Microsoft Office.
