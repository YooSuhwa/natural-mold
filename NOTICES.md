# Third-Party Notices

This file records third-party code, data, assets, and dependency license notes
that are relevant to distributing Moldy (`natural-mold`). Moldy's own source is
licensed under the MIT License; third-party materials remain under their
respective licenses.

This notice is not legal advice. For dependency-level details, inspect
`backend/pyproject.toml`, `backend/uv.lock`, `frontend/package.json`, and
`frontend/pnpm-lock.yaml`.

---

## Included Code And Assets

| Material | Location | Source | License |
|---|---|---|---|
| AgentPrism UI components | `frontend/src/components/agent-prism/` | Evil Martians AgentPrism UI components | MIT |
| Pretendard variable font | `frontend/src/app/fonts/PretendardVariable.woff2` | Pretendard by Kil Hyung-jin (`orioncactus/pretendard`) | SIL Open Font License 1.1 |

### AgentPrism

`frontend/src/components/agent-prism/` includes MIT-licensed AgentPrism UI
component code.
Copyright (c) 2025 Evil Martians, Yuri Mikhin, Ivan Eltsov, Gleb Stroganov,
Kirill Yakovenko.

- <https://github.com/evilmartians/agent-prism>
- <https://github.com/evilmartians/agent-prism/blob/main/LICENSE>

### Pretendard

`frontend/src/app/fonts/PretendardVariable.woff2` is licensed under the SIL Open
Font License, Version 1.1. Reserved font name: "Pretendard".
Copyright (c) 2021, Kil Hyung-jin.

- <https://github.com/orioncactus/pretendard>
- <https://github.com/orioncactus/pretendard/blob/main/LICENSE>

## Dependency License Notes

The following packages are not vendored Moldy source, but are notable because
their licenses may require notice or distribution care when bundled in release
artifacts.

| Package / family | Used by | License note | Distribution note |
|---|---|---|---|
| `psycopg`, `psycopg-pool`, `psycopg-binary` | LangGraph Postgres checkpointer / backend runtime | LGPL-3.0-only | Required for current Postgres checkpointer path. Keep license notices when distributing backend bundles or containers. |
| `certifi` | outbound TLS verification via backend HTTP clients | MPL-2.0 | Directly imported in backend TLS setup and also pulled transitively by HTTP clients. |
| `orjson` | backend SSE serialization hot path | MPL-2.0 and Apache-2.0/MIT components | Direct backend dependency; can be replaced by stdlib JSON if this license note is undesirable. |
