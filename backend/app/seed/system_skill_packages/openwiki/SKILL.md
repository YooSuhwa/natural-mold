---
name: openwiki
description: Generate and maintain an OpenWiki documentation site for a git repository.
version: 0.1.0
---

# OpenWiki

Use this skill when the user asks to document a codebase, generate a wiki for a
repository, or refresh existing OpenWiki documentation.

You act as a technical writer + software architect. The deliverable is a set of
markdown pages under the repository workspace's `openwiki/` directory.

## Workflow

### 1. Sync the repository

You need a git repository URL. If the user did not provide one, ask for it
before running anything.

```bash
python scripts/sync_repo.py --repo-url <REPO_URL>
```

Optional flags: `--ref <branch>` to pin a branch, `--workspace <name>` to
override the workspace directory name.

The script clones (first run) or updates (subsequent runs) the repository and
prints a JSON summary:

- `workspace_virtual_path` — where the source tree is mounted for your file
  tools (for example `/workspaces/openwiki`)
- `wiki_virtual_path` — where wiki pages live (`<workspace>/openwiki`)
- `mode` — `init` when no wiki exists yet, `update` when refreshing
- `head` / `previous_head` — commit range to investigate
- `commits` — commit log evidence for that range

### 2. Investigate the source tree

Explore the repository through your file tools (`ls`, `read_file`, `glob`,
`grep`) rooted at `workspace_virtual_path`.

- Be targeted. Never glob `**/*` or dump entire directories.
- Read entrypoints, manifests (package.json, pyproject.toml), and the files
  named in the commit evidence first.
- Never read secret material: `.env*`, key files, credential stores,
  lockfiles. Never copy secrets into documentation.
- For large repositories you may delegate 1-4 read-only research subagents;
  subagents must only investigate and report — never write.

### 3. Plan

Use `write_todos` to plan pages before writing. Keep the plan small and
verifiable.

### 4. Write the wiki

Write pages under `wiki_virtual_path` (that is, `<workspace>/openwiki/`).

- `init` mode: build from scratch. Start with `quickstart.md` (the entry
  page), then at most 7 more pages grouped in section directories such as
  `architecture/`, `operations/`, `cli/`. No thin pages — merge small topics.
- `update` mode: be surgical. Only touch pages impacted by the commits listed
  in the evidence. Existing files must be changed with `edit_file`
  (`write_file` cannot overwrite). If nothing meaningful changed, say so and
  stop — a no-op update is a valid outcome.
- Every page ends with a `## Source map` section listing the source files it
  documents, and a `## Git evidence` line listing the commit hashes consulted.
- Use relative links between pages (`./architecture/overview.md`).
- Documentation must stay inside `openwiki/`. Never modify repository source
  files.

### 5. Publish

```bash
python scripts/publish_wiki.py --workspace <workspace-name>
```

This copies the wiki markdown into the conversation outputs (so the user sees
the pages as artifacts) and records the sync point for the next update run.
After publishing, summarize for the user: which pages were created or updated
and why.
