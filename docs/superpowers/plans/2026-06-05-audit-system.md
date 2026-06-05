# Audit System Development Plan

## Goal

Moldy에 사용자별/관리자용 audit 기능을 추가한다.

- 일반 사용자는 `Settings > 활동 기록`에서 본인 관련 audit event만 조회한다.
- `super_user`는 같은 개인 화면을 유지하면서, 관리자 항목의 `전체 활동 기록`에서 모든 사용자의 event를 조회한다.
- event는 누가, 언제, 어떤 기능을, 어떤 대상에 대해 실행했고, 성공/실패/차단 여부와 실패 사유를 추적할 수 있어야 한다.
- secret, prompt 본문, message 본문, API key cleartext, share token 전체값은 audit metadata에 저장하지 않는다.

## OpenTelemetry와의 관계

OpenTelemetry는 trace/metric/log 상관관계와 성능 관측에 적합하다. 이 기능은 사용자에게 노출되는 제품 데이터이며 권한/소유권/검색/보존 정책이 필요하므로 애플리케이션 DB에 직접 구현한다. `request_id`, `trace_id`, `run_id`를 event에 저장해 추후 OTel/Langfuse trace와 연결할 수 있게 한다.

## Backend Design

### Table

새 테이블 `audit_events`를 추가한다.

주요 컬럼:

- actor: `actor_type`, `actor_user_id`, `actor_api_key_id`, email/label snapshot
- owner: `owner_user_id`, email snapshot
- target: `target_type`, `target_id`, name snapshot, target owner
- result: `action`, `outcome`, `reason_code`, `reason_message`
- correlation: `request_id`, `trace_id`, `run_id`
- request context: `ip_address`, `user_agent`
- sanitized metadata: JSON `metadata`
- `created_at`

조회 정책:

- `scope=mine`: `owner_user_id`, `actor_user_id`, `target_owner_user_id` 중 현재 사용자와 연결된 event만 조회
- `scope=all`: `super_user`만 허용
- cursor pagination: `(created_at, id)` 내림차순

### API

`GET /api/audit-events`

필터:

- `scope=mine|all`
- `limit`, `cursor`
- `action`, `target_type`, `outcome`
- `actor_user_id`, `owner_user_id`
- `request_id`, `trace_id`, `run_id`
- `created_from`, `created_to`

### Recorded Domains

Implemented audit domains:

- Auth: register, login success/failure, logout, refresh, profile/avatar changes
- Credentials: create/update/delete/test and existing credential audit bridge
- Agents: create/update/favorite/delete
- Tools: create/update/delete/run
- MCP: create/from-registry/probe/import/update/delete/test/discover
- Skills: create/upload/update/content/file/binding/delete
- Triggers: create/update/delete/run-now
- Conversations: create/update/read/delete, message send/resume/edit/regenerate/switch branch
- Share links: create/revoke without full token
- Agent API control plane: deployment create/update, key create/revoke without cleartext key
- Public Agent Runtime API: thread create, wait/stream runs by API key
- Models: create/update/delete
- System LLM settings: role update
- Marketplace: install/update/uninstall, publish/version publish, item patch, ACL, disable/enable, admin listing
- Permission/CSRF denials through the application error handler

Sensitive data handling:

- Metadata is redacted through `audit_service.sanitize_metadata`.
- Prompt/message/skill content is represented by boolean flags, counts, key lists, or lengths.
- MCP headers/env values are represented by key names only.
- API keys and share tokens are never stored in full.

## Frontend Design

Routes:

- `/settings/audit`: personal scope (`mine`)
- `/settings/admin/audit`: super_user-only all-user scope (`all`)

Navigation:

- Account section: `활동 기록`
- Admin section, only for `super_user`: `전체 활동 기록`

UI pattern:

- Operational, dense settings view rather than a marketing page.
- Summary metrics for loaded success/failure/denied counts.
- Filter bar for action, target type, outcome, request id, run id.
- Cursor-based “load more”.
- Event list with time, outcome, action, target, actor, request/run id.
- Detail pane with actor/target/correlation fields and formatted redacted metadata JSON.

## Verification Plan

Backend:

- Unit/integration tests for core audit service and endpoint authorization.
- TDD coverage for representative mutation domains.
- Regression suite for changed routers.
- `ruff check` on touched backend files.

Frontend:

- `pnpm lint`
- `pnpm lint:design-system`
- `pnpm build` or documented reason if blocked by pre-existing type/build issues.

Browser E2E:

- Start backend and frontend with matched ports/CORS/API base.
- Log in as seeded super_user.
- Create actions that generate audit events.
- Verify `/settings/audit` shows personal events and filters.
- Verify `/settings/admin/audit` is visible to super_user and shows all scope.
- Verify detail pane does not expose sensitive values.
