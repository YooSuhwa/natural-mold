# Artifact Preview Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LLM/skill 실행 중 생성된 파일을 최종 답변의 Markdown 링크에만 의존하지 않고, SSE 파일 이벤트와 우측 Artifact Panel로 즉시 발견, 목록화, preview, 다운로드할 수 있게 만든다.

**Architecture:** Moldy의 기존 Deep Agents runtime, `execute_in_skill`, `message_events` SSE persistence, conversation ownership 모델을 유지한다. 파일은 DB artifact manifest와 storage backend가 관리하고, UI는 event-driven right rail과 preview provider registry로 확장한다.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Deep Agents `create_deep_agent`, LangGraph checkpointer, Next.js 16, React 19, Jotai, TanStack Query, EventSource SSE, existing `react-markdown`, `mermaid`, `react-syntax-highlighter`.

---

## 1. 배경과 핵심 판단

현재 Moldy는 스킬 실행 결과 파일을 `OUTPUT_FILES` 텍스트와 `/api/conversations/{conversation_id}/files/{file_path}` 링크 중심으로 노출한다. 이 방식은 구현이 단순하지만 사용자는 최종 답변을 받기 전까지 어떤 파일이 생성되었는지 알기 어렵고, 여러 후보 이미지, 리포트 초안, 차트, CSV, PDF 같은 산출물을 채팅 옆에서 지속적으로 탐색하기 어렵다.

이번 기능의 핵심은 "LLM이 파일을 만들었다"를 답변 텍스트 안 링크로만 보여주는 것이 아니라, 파일 생성/수정 자체를 런타임 이벤트로 승격시키는 것이다.

```text
execute_in_skill writes files
-> backend detects artifact delta
-> backend persists artifact manifest/version
-> SSE emits file_event
-> frontend artifact store updates
-> right rail ArtifactPanel renders tree + preview
```

LangChain/Deep Agents 관점에서는 새 LangGraph runtime을 따로 만들 필요가 없다. `framework-selection` 기준으로 이 기능은 장기 실행 agent, tool call, skill, filesystem, persistence가 결합된 기능이므로 기존 Deep Agents 기반 top-level runtime을 유지하는 것이 맞다. `deep-agents-core` 기준으로도 file management, skills, checkpointer는 deepagents 설정과 주변 서비스를 통해 확장해야 하며, 별도 agent runtime을 재작성하면 Moldy의 `message_events`, checkpointer, credential, permission 흐름과 중복된다.

## 2. 실제 코드 감사 요약

### Backend

- [backend/app/config.py](/Users/chester/dev/ref/natural-mold/backend/app/config.py): `conversation_output_dir = "./data/conversations"`, `upload_dir = "./data/uploads"`가 있다. artifact storage 설정은 아직 없다.
- [backend/app/marketplace/skill_runtime.py](/Users/chester/dev/ref/natural-mold/backend/app/marketplace/skill_runtime.py): `output_dir = data/conversations/{thread_id}`로 스킬 산출물 위치를 정한다.
- [backend/app/agent_runtime/executor.py](/Users/chester/dev/ref/natural-mold/backend/app/agent_runtime/executor.py): `execute_in_skill`이 Python/curl 실행을 제한적으로 허용하고, 실행 후 output dir 파일 목록을 `OUTPUT_FILES`로 붙인다. 가장 현실적인 1차 artifact 감지 지점이다.
- [backend/app/agent_runtime/streaming.py](/Users/chester/dev/ref/natural-mold/backend/app/agent_runtime/streaming.py): `emit()`이 SSE 전송, `EventBroker` publish, trace sink, DB persistence flush를 한 번에 처리한다. `file_event`도 이 경로를 타야 resume과 message event 저장이 자연스럽다.
- [backend/app/agent_runtime/event_names.py](/Users/chester/dev/ref/natural-mold/backend/app/agent_runtime/event_names.py): SSE event name 상수 파일이다. `FILE_EVENT = "file_event"`를 추가해야 한다.
- [backend/app/models/message_event.py](/Users/chester/dev/ref/natural-mold/backend/app/models/message_event.py): assistant turn의 event stream을 append-only로 저장한다. 파일 이벤트 replay에 적합하다.
- [backend/app/models/message_attachment.py](/Users/chester/dev/ref/natural-mold/backend/app/models/message_attachment.py): 사용자 업로드 input file 모델이다. artifact는 LLM/runtime output file이므로 별도 모델로 분리하는 것이 안전하다.
- [backend/app/routers/uploads.py](/Users/chester/dev/ref/natural-mold/backend/app/routers/uploads.py): 업로드 파일은 UUID 기반 local disk에 저장한다. S3 전환 시 storage backend만 바꾸겠다는 주석이 있어 artifact도 같은 방향을 따를 수 있다.
- [backend/app/routers/conversations.py](/Users/chester/dev/ref/natural-mold/backend/app/routers/conversations.py): 기존 파일 다운로드 endpoint가 `data/conversations/{conversation_id}`에서 파일을 서빙하고 image preview를 만든다. artifact API를 추가하면서 이 endpoint의 ownership guard도 함께 점검해야 한다.
- [backend/app/routers/shares.py](/Users/chester/dev/ref/natural-mold/backend/app/routers/shares.py), [backend/app/services/share_service.py](/Users/chester/dev/ref/natural-mold/backend/app/services/share_service.py): 공유 링크는 conversation snapshot 중심이다. artifact는 share token에서 접근 가능한 별도 public read endpoint가 필요하다.

### Frontend

- [frontend/package.json](/Users/chester/dev/ref/natural-mold/frontend/package.json): 이미 `mermaid`, `react-markdown`, `react-syntax-highlighter`가 있다. Markdown, Mermaid, code preview의 1차 구현은 추가 라이브러리 없이 가능하다.
- [frontend/src/lib/types/index.ts](/Users/chester/dev/ref/natural-mold/frontend/src/lib/types/index.ts): `SSEEventType`, `SSEEvent` union에 `file_event` 타입이 없다.
- [frontend/src/lib/sse/parse-sse.ts](/Users/chester/dev/ref/natural-mold/frontend/src/lib/sse/parse-sse.ts): SSE parser는 generic하게 event를 파싱하므로 새 event type 추가가 작다.
- [frontend/src/lib/chat/use-chat-runtime.ts](/Users/chester/dev/ref/natural-mold/frontend/src/lib/chat/use-chat-runtime.ts): SSE event switch에 `file_event` 처리를 추가해야 한다.
- [frontend/src/lib/stores/chat-right-rail.ts](/Users/chester/dev/ref/natural-mold/frontend/src/lib/stores/chat-right-rail.ts): right rail mode가 `none | subagent | tool-result | outline`이다. `artifacts`를 추가한다.
- [frontend/src/components/chat/right-rail/chat-right-rail.tsx](/Users/chester/dev/ref/natural-mold/frontend/src/components/chat/right-rail/chat-right-rail.tsx): 우측 패널 shell이 이미 있다. `ArtifactPanelContent`를 추가하기 좋은 위치다.
- [frontend/src/components/chat/markdown-content.tsx](/Users/chester/dev/ref/natural-mold/frontend/src/components/chat/markdown-content.tsx): inline Markdown, image, Mermaid 렌더링이 이미 있다. 이 기능은 유지하고, side artifact panel은 "파일로 생성된 durable artifact"에 한정한다.
- [frontend/src/lib/chat/tool-ui-registry.ts](/Users/chester/dev/ref/natural-mold/frontend/src/lib/chat/tool-ui-registry.ts): tool result UI registry가 있다. Artifact preview registry는 tool UI와 분리하되 패턴을 참고할 수 있다.

## 3. 차용할 패턴

### LambChat류 파일/문서 preview 패턴

차용할 점은 "생성 파일을 별도 파일 라이브러리처럼 보여주는 UX"다. 다만 PDF, 문서, CAD, 프로젝트 preview가 모두 단일 라이브러리로 해결되는 구조는 아니다. 일반적으로 파일 타입별 provider가 다르고, 고급 문서 preview는 서버 변환 또는 전용 viewer가 필요하다.

Moldy에 바로 가져올 수 있는 것은 다음이다.

- 채팅 본문 inline preview와 우측 durable artifact preview를 분리한다.
- 파일 확장자/MIME 기반 provider registry를 둔다.
- preview 불가 파일도 metadata, download, open original을 안정적으로 제공한다.
- 장기적으로 PDF, Office, CAD, project preview를 provider plugin으로 붙일 수 있게 한다.

### JoySafeter류 file event 패턴

차용할 점은 backend가 파일 write/update/delete를 UI event로 승격시키는 흐름이다.

Moldy에서는 WebSocket/run model을 그대로 복사하지 않는다. 이미 SSE, `message_events`, `EventBroker`, broker resume이 있으므로 `file_event`를 기존 stream에 넣는 것이 최적이다.

1차 구현은 sandbox write proxy 없이도 가능하다. `execute_in_skill` 실행 전후 output dir snapshot을 비교해 새 파일/수정 파일을 artifact로 ingest하고, tool result 직후 `file_event`를 emit한다. 이후 필요하면 output dir polling, 최종적으로 sandbox/file backend write proxy로 실시간성을 높인다.

## 4. 설계 원칙

1. **MessageAttachment와 Artifact를 분리한다.**  
   `MessageAttachment`는 사용자 입력 파일이다. `ConversationArtifact`는 agent/runtime 출력 파일이다. 권한, lifecycle, share, versioning 요구가 다르다.

2. **Event log는 source of truth가 아니다.**  
   `message_events`는 replay와 UI stream용이다. artifact 목록의 source of truth는 DB manifest와 storage object다.

3. **LLM이 제안한 path를 storage path로 신뢰하지 않는다.**  
   UI 표시용 `logical_path`와 실제 저장 위치 `object_key`를 분리한다. 실제 storage key는 UUID artifact id/version id 기반이다.

4. **local-first, S3/MinIO-ready로 간다.**  
   1차 구현은 local disk로 충분하다. 단 storage interface를 먼저 만들고 API는 storage backend에 의존하지 않게 설계한다.

5. **우측 패널은 durable file artifact 전용이다.**
   기존 inline Markdown image, Mermaid, code rendering은 유지한다. 답변 본문에 포함된 inline content까지 모두 artifact panel로 옮기지 않는다.

6. **preview는 addon/provider registry로 확장한다.**
   PDF, Office, CAD, Excalidraw, Mermaid, code는 각각 다른 provider가 맡는다. 단일 viewer 라이브러리에 묶지 않는다.

7. **Generated File Library를 1차 범위에 포함한다.**
   Artifact Panel만 먼저 만들고 전역 파일 라이브러리를 뒤로 미루면 DB/API/UI를 다시 뜯게 된다. `conversation_artifacts`를 처음부터 대화별 패널과 전역 Generated Files Library가 함께 쓰는 단일 인덱스로 설계한다.

## 5. Backend 설계

### 5.1 데이터 모델

새 테이블을 추가한다.

```text
conversation_artifacts
- id UUID PK
- user_id UUID FK users.id NOT NULL
- agent_id UUID FK agents.id NOT NULL  # library 필터/통계용 denormalized key
- conversation_id UUID FK conversations.id NOT NULL
- assistant_msg_id TEXT NOT NULL  # stream_agent_response run_id, message_events.assistant_msg_id와 동일
- run_id TEXT NOT NULL            # assistant_msg_id alias; API/event에서는 run_id로 노출
- tool_call_id TEXT NULL
- source_tool_name TEXT NULL
- logical_path TEXT NOT NULL
- display_name TEXT NOT NULL
- extension TEXT NULL
- mime_type TEXT NOT NULL
- artifact_kind TEXT NOT NULL  # image | video | audio | pdf | markdown | html | code | document | data | cad | other
- size_bytes BIGINT NOT NULL
- sha256 TEXT NOT NULL
- current_version_id UUID NULL
- status TEXT NOT NULL  # writing | ready | deleted | failed
- is_favorite BOOLEAN NOT NULL DEFAULT false
- last_opened_at TIMESTAMPTZ NULL
- preview_count INTEGER NOT NULL DEFAULT 0
- download_count INTEGER NOT NULL DEFAULT 0
- branch_checkpoint_id TEXT NULL
- linked_message_ids JSONB NULL   # message_events.linked_message_ids snapshot, UI message 매칭용
- metadata_json JSONB NOT NULL DEFAULT '{}'
- created_at TIMESTAMPTZ NOT NULL
- updated_at TIMESTAMPTZ NOT NULL
```

```text
artifact_versions
- id UUID PK
- artifact_id UUID FK conversation_artifacts.id NOT NULL
- version_number INTEGER NOT NULL
- storage_provider TEXT NOT NULL  # local | s3
- bucket TEXT NULL
- object_key TEXT NOT NULL
- original_filename TEXT NOT NULL
- size_bytes BIGINT NOT NULL
- sha256 TEXT NOT NULL
- metadata_json JSONB NOT NULL DEFAULT '{}'
- created_at TIMESTAMPTZ NOT NULL
```

권장 constraint/index:

- `conversation_artifacts(conversation_id, assistant_msg_id, logical_path)` unique
- `conversation_artifacts(user_id, conversation_id, created_at)`
- `conversation_artifacts(conversation_id, assistant_msg_id, updated_at)`
- `conversation_artifacts(user_id, created_at)`
- `conversation_artifacts(user_id, agent_id, created_at)`
- `conversation_artifacts(user_id, artifact_kind, created_at)`
- partial index: `conversation_artifacts(user_id, created_at) WHERE is_favorite = true`
- `artifact_versions(artifact_id, version_number)` unique

`run_id`별 path unique를 1차 기준으로 잡는다. 같은 `report/final.md`가 다른 실행에서 다시 생성되는 경우 별도 artifact로 보여주고, UI에서 실행 단위로 그룹화한다. 추후 "같은 logical path를 conversation-level 문서로 version merge"하는 정책은 별도 UX 결정 후 확장한다.

`agent_id`는 `conversation -> agent` join으로도 얻을 수 있지만 Generated Files Library의 agent filter와 통계를 위해 denormalize한다. artifact ingest 시 router가 이미 `_resolve_agent_context()`로 `cfg.agent_id`를 알고 있으므로 recorder context에 함께 넣는다.

`is_favorite`, `last_opened_at`, `preview_count`, `download_count`는 "라이브러리 기능을 나중에 붙일 때"가 아니라 1차부터 넣는다. 패널과 라이브러리 모두 같은 artifact row를 보므로 즐겨찾기와 최근 열람 상태가 일관된다.

### 5.2 Storage 모델

1차 local storage:

```text
data/artifacts/conversations/{conversation_id}/{artifact_id}/v{version_number}/{safe_filename}
```

S3/MinIO storage:

```text
bucket: moldy-artifacts
key: conversations/{conversation_id}/{artifact_id}/v{version_number}/{safe_filename}
```

기존 `data/conversations/{conversation_id}`는 skill runtime의 staging/output directory로 유지한다. artifact service가 새/수정 파일을 발견하면 canonical artifact storage로 복사하고 DB manifest를 쓴다.

추가 settings:

```text
ARTIFACT_STORAGE_BACKEND=local
ARTIFACT_STORAGE_DIR=./data/artifacts
ARTIFACT_MAX_BYTES=104857600
ARTIFACT_PREVIEW_MAX_TEXT_BYTES=1048576
ARTIFACT_S3_ENDPOINT_URL=
ARTIFACT_S3_BUCKET=
ARTIFACT_S3_ACCESS_KEY_ID=
ARTIFACT_S3_SECRET_ACCESS_KEY=
```

처음에는 S3 설정을 실제 구현하지 않아도 `StorageBackend` interface와 config shape를 맞춰두면 MinIO 전환 비용이 낮다.

### 5.3 Path와 filename 규칙

`logical_path`는 skill output dir 기준 상대 경로다. 다음을 강제한다.

- absolute path 금지
- `..` segment 금지
- null byte, control character 금지
- segment 길이 제한
- 전체 path 길이 제한
- 숨김/시스템 파일 제외 옵션: `.DS_Store`, `__pycache__`, preview cache
- symlink는 follow하지 않음

실제 storage key는 artifact UUID와 version number로 만든다. LLM이 만든 파일명은 `display_name`, `original_filename`, `logical_path`로만 보존한다.

### 5.4 Event schema

`event_names.py`에 `FILE_EVENT = "file_event"`를 추가한다.

```json
{
  "event": "file_event",
  "data": {
    "op": "created",
    "id": "uuid",
    "version_id": "uuid",
    "version_number": 1,
    "agent_id": "uuid",
    "conversation_id": "uuid",
    "assistant_msg_id": "uuid-string",
    "run_id": "uuid",
    "tool_call_id": "call_...",
    "source_tool_name": "execute_in_skill",
    "path": "report/final.md",
    "display_name": "final.md",
    "mime_type": "text/markdown",
    "extension": "md",
    "artifact_kind": "markdown",
    "size_bytes": 18342,
    "sha256": "64-char-hex",
    "status": "ready",
    "is_favorite": false,
    "last_opened_at": null,
    "preview_count": 0,
    "download_count": 0,
    "agent_name": null,
    "conversation_title": null,
    "url": "/api/conversations/{conversation_id}/artifacts/{artifact_id}",
    "preview_url": "/api/conversations/{conversation_id}/artifacts/{artifact_id}/content",
    "download_url": "/api/conversations/{conversation_id}/artifacts/{artifact_id}/download"
  }
}
```

`op` 값:

- `created`: 새 logical path 생성
- `updated`: 같은 run/logical path의 새 version 생성
- `deleted`: artifact 삭제 또는 더 이상 접근 불가
- `failed`: ingest 또는 preview 준비 실패

### 5.5 Artifact service

새 서비스 파일:

- `backend/app/models/conversation_artifact.py`
- `backend/app/schemas/artifact.py`
- `backend/app/services/artifact_storage.py`
- `backend/app/services/artifact_service.py`
- `backend/app/routers/artifacts.py`

핵심 함수:

```text
snapshot_output_dir(base_dir) -> ArtifactSnapshot
diff_snapshots(before, after) -> list[ArtifactDelta]
ingest_output_dir_delta(conversation_id, user_id, run_id, source_tool_name, tool_call_id, before, after) -> list[FileEventPayload]
list_artifacts(conversation_id, user_id) -> list[ArtifactSummary]
read_artifact_content(artifact_id, user_id, max_bytes) -> ArtifactContent
open_artifact_stream(artifact_id, user_id) -> StreamingResponse
```

`snapshot_output_dir`는 path, size, mtime_ns, sha256을 기록한다. 작은 파일은 sha256까지 즉시 계산하고, 큰 파일은 size/mtime으로 1차 감지 후 ingest 시 streaming hash를 계산한다.

### 5.6 Stream 통합

1차 구현의 최적 경로는 `streaming.py`의 기존 `emit()`를 그대로 쓰는 것이다. `execute_in_skill` tool result 직후 artifact delta를 ingest하고 `file_event`를 emit하면 broker resume, trace persistence, `message_events` 저장이 자동으로 따라온다.

구현 방향:

- `AgentConfig` 또는 stream context에 `run_id`, `user_id`, `conversation_id`, `artifact_output_dir`를 명시적으로 포함한다.
- `stream_agent_response` 시작 시 output dir snapshot을 만든다.
- `tool_call_result` event에서 tool name이 `execute_in_skill`이면 현재 output dir을 다시 snapshot한다.
- 이전 snapshot과 비교해 artifact service가 DB/storage ingest를 수행한다.
- 생성된 event payload를 `emit(FILE_EVENT, payload)`로 흘린다.
- snapshot 기준점을 갱신한다.

이 방식은 `execute_in_skill` 내부에서 SSE를 직접 emit하려고 하지 않으므로 현재 구조에 덜 침투적이다. near-real-time이 필요해지면 tool 실행 중 polling task나 file backend proxy를 붙이는 2차 작업으로 확장한다.

### 5.7 API 설계

인증된 conversation API:

```text
GET /api/conversations/{conversation_id}/artifacts
GET /api/conversations/{conversation_id}/artifacts/{artifact_id}
GET /api/conversations/{conversation_id}/artifacts/{artifact_id}/content?version=current
GET /api/conversations/{conversation_id}/artifacts/{artifact_id}/download?version=current
DELETE /api/conversations/{conversation_id}/artifacts/{artifact_id}
```

인증된 generated file library API:

```text
GET /api/artifacts?q=&agent_id=&conversation_id=&kind=&favorite=&limit=&cursor=
GET /api/artifacts/stats
GET /api/artifacts/recent?limit=
PATCH /api/artifacts/{artifact_id}
POST /api/artifacts/{artifact_id}/opened
GET /api/artifacts/{artifact_id}/content?version=current
GET /api/artifacts/{artifact_id}/download?version=current
```

`GET /api/artifacts`는 현재 사용자 소유 artifact 전체를 대상으로 검색한다. `q`는 `display_name`, `logical_path`를 대상으로 하고, `agent_id`, `conversation_id`, `kind`, `favorite`는 AND 필터로 적용한다. `limit/cursor`는 conversation list와 같은 cursor pagination 스타일을 사용한다.

`PATCH /api/artifacts/{artifact_id}`는 1차에서 `{"is_favorite": true|false}`만 허용한다. 이름 변경, 이동, 태그 편집은 별도 제품 결정 후 확장한다.

`POST /api/artifacts/{artifact_id}/opened`는 preview/open action에서 호출하고 `last_opened_at`, `preview_count`를 갱신한다. `download` endpoint는 파일을 반환하기 전에 `download_count`를 증가시킨다.

`GET /api/artifacts/stats`는 최소한 다음을 반환한다.

```json
{
  "total_count": 42,
  "total_size_bytes": 1234567,
  "favorite_count": 3,
  "by_kind": [
    { "kind": "markdown", "count": 10, "size_bytes": 12000 }
  ],
  "recent_count_7d": 8
}
```

공유 링크 API:

```text
GET /api/shares/{token}/artifacts
GET /api/shares/{token}/artifacts/{artifact_id}
GET /api/shares/{token}/artifacts/{artifact_id}/content
GET /api/shares/{token}/artifacts/{artifact_id}/download
```

모든 authenticated endpoint는 conversation owner를 확인한다. 기존 `/api/conversations/{conversation_id}/files/{file_path}`는 backward compatibility로 유지할 수 있지만, 첫 milestone에서 owner guard를 강화하거나 artifact API로 대체하는 방향을 명확히 한다.

### 5.8 Security와 권한

- artifact API는 반드시 `get_owned_conversation_with_agent` 또는 동등한 owner guard를 사용한다.
- public share endpoint는 share token과 snapshot에 포함된 artifact만 허용한다.
- storage object path와 local absolute path는 API 응답에 노출하지 않는다.
- HTML preview는 sandboxed iframe으로만 제공한다.
- SVG는 script/event handler 위험이 있으므로 image preview로 inline하지 않거나 sanitize한다.
- Markdown preview는 raw HTML을 비활성화한다.
- text preview는 byte limit와 line limit를 둔다.
- artifact ingest는 max file size, max files per run, max total bytes per conversation quota를 적용한다.
- 삭제는 DB status `deleted` 후 storage garbage collection으로 처리하는 soft-delete 우선이 안전하다.

## 6. Frontend 설계

### 6.1 State와 SSE

추가 타입:

- `ArtifactSummary`
- `ArtifactVersion`
- `FileEventPayload`
- `ArtifactPreviewKind`

수정 지점:

- `frontend/src/lib/types/index.ts`: `SSEEventType`에 `file_event` 추가
- `frontend/src/lib/chat/use-chat-runtime.ts`: `file_event` 수신 시 artifact store update
- `frontend/src/lib/stores/chat-artifacts.ts`: conversation/run별 artifact 목록 상태
- `frontend/src/lib/hooks/use-conversation-artifacts.ts`: 새로고침/진입 시 artifact list fetch

SSE로 들어온 event는 optimistic state update처럼 반영하고, conversation 진입 시 API list로 authoritative sync를 맞춘다.

### 6.2 Right rail

수정 지점:

- `frontend/src/lib/stores/chat-right-rail.ts`: `RightRailMode`에 `artifacts` 추가
- `frontend/src/components/chat/right-rail/chat-right-rail.tsx`: `ArtifactPanelContent` 렌더링
- `frontend/src/components/chat/right-rail/artifact-panel-content.tsx`: 신규
- `frontend/src/components/chat/right-rail/artifact-preview.tsx`: 신규

패널 UX:

- 실행/run별 그룹
- 파일 트리 또는 compact list
- 생성/수정 상태 표시
- MIME 아이콘
- preview 영역
- download/open controls
- unsupported preview fallback

### 6.3 Preview provider registry

초기 구조:

```ts
type ArtifactPreviewProvider = {
  id: string
  priority: number
  match: (artifact: ArtifactSummary) => boolean
  render: (props: ArtifactPreviewProps) => React.ReactNode
  maxBytes?: number
}
```

권장 파일:

- `frontend/src/components/chat/artifacts/preview-registry.ts`
- `frontend/src/components/chat/artifacts/providers/image-preview.tsx`
- `frontend/src/components/chat/artifacts/providers/media-preview.tsx`
- `frontend/src/components/chat/artifacts/providers/markdown-preview.tsx`
- `frontend/src/components/chat/artifacts/providers/mermaid-preview.tsx`
- `frontend/src/components/chat/artifacts/providers/code-preview.tsx`
- `frontend/src/components/chat/artifacts/providers/text-preview.tsx`
- `frontend/src/components/chat/artifacts/providers/fallback-preview.tsx`

1차 provider:

- Image: browser native `<img>`
- Video/audio: browser native controls
- Markdown: existing `MarkdownContent` 재사용
- Mermaid: existing `mermaid` dependency 재사용
- Code/text/json/csv: existing syntax highlighter 또는 lightweight text viewer
- HTML: sandboxed iframe, default disabled 또는 explicit open
- PDF: first pass는 browser iframe/open original, later `react-pdf` or pdf.js
- Excalidraw: later `@excalidraw/excalidraw`
- Office/CAD: later server conversion or specialized provider

중요한 기준은 "기존 inline preview 기능을 제거하지 않는다"이다. Markdown 답변 안의 Mermaid/code/image는 계속 inline이고, 우측 panel provider는 artifact file에만 적용한다.

### 6.4 Addon 확장성

외부 preview provider를 쉽게 추가하려면 core registry API를 작게 유지한다.

- provider는 `mime_type`, `extension`, `metadata_json`을 기준으로 match한다.
- provider priority로 충돌을 해결한다.
- provider는 lazy import를 허용한다.
- heavy dependency는 provider 단위 chunk로 분리한다.
- untrusted file rendering은 provider가 직접 iframe sandbox 또는 sanitize 정책을 선언하게 한다.

처음부터 외부 npm plugin loading까지 열 필요는 없다. 1차는 코드 레벨 addon registry로 충분하다. 런타임 외부 플러그인 로딩은 보안 모델, dependency isolation, CSP까지 필요하므로 별도 threat model 이후가 맞다.

## 7. 구현 단계

### Milestone 1: Backend artifact foundation

- [ ] Alembic migration으로 `conversation_artifacts`, `artifact_versions` 추가
- [ ] SQLAlchemy model 추가
- [ ] Pydantic schema 추가
- [ ] `ArtifactStorageBackend` interface 추가
- [ ] `LocalArtifactStorageBackend` 구현
- [ ] path sanitization utility 구현
- [ ] artifact service의 snapshot/diff/ingest 구현
- [ ] quota와 max bytes 설정 추가

### Milestone 2: SSE file_event

- [ ] `event_names.py`에 `FILE_EVENT` 추가
- [ ] stream context에 `run_id`, `conversation_id`, `user_id`, `artifact_output_dir` 명시
- [ ] `execute_in_skill` tool result 후 output dir delta ingest
- [ ] ingest 결과를 `emit(FILE_EVENT, payload)`로 전송
- [ ] `message_events` persistence/replay에서 `file_event`가 보존되는지 검증
- [ ] 기존 `OUTPUT_FILES` 텍스트는 compatibility로 유지

### Milestone 3: Artifact API

- [ ] `GET /api/conversations/{id}/artifacts`
- [ ] `GET /api/conversations/{id}/artifacts/{artifact_id}`
- [ ] `GET /api/conversations/{id}/artifacts/{artifact_id}/content`
- [ ] `GET /api/conversations/{id}/artifacts/{artifact_id}/download`
- [ ] `GET /api/artifacts` 전역 generated file library 목록/검색
- [ ] `GET /api/artifacts/stats` 파일 통계
- [ ] `GET /api/artifacts/recent` 최근 열람/생성 파일
- [ ] `PATCH /api/artifacts/{artifact_id}` favorite 토글
- [ ] `POST /api/artifacts/{artifact_id}/opened` 최근 열람/preview count 기록
- [ ] share token artifact read API
- [ ] 기존 conversation file endpoint owner guard 점검/수정
- [ ] content disposition, MIME, cache headers 정리

### Milestone 4: Frontend event/store/right rail

- [ ] TS SSE type에 `file_event` 추가
- [ ] `chat-artifacts` Jotai store 추가
- [ ] `useConversationArtifacts` query 추가
- [ ] `use-chat-runtime`에서 `file_event` 처리
- [ ] right rail mode에 `artifacts` 추가
- [ ] `ArtifactPanelContent` 추가
- [ ] tool result나 toolbar에서 artifact panel 열기 affordance 추가

### Milestone 5: Preview providers

- [ ] preview registry 구현
- [ ] image/video/audio provider
- [ ] markdown provider
- [ ] mermaid provider
- [ ] code/text/json/csv provider
- [ ] fallback/download provider
- [ ] HTML sandbox preview 정책 결정 후 구현
- [ ] PDF first-pass preview 구현

### Milestone 6: Generated File Library UI

- [ ] `/artifacts` route 추가
- [ ] sidebar navigation에 Files/Artifacts 항목 추가
- [ ] 파일 검색, agent filter, conversation filter, kind filter, favorite filter
- [ ] 파일 목록/그리드, preview rail 또는 detail pane
- [ ] favorite toggle
- [ ] 최근 열람/생성 섹션
- [ ] total size, kind breakdown, favorite count 통계 표시
- [ ] ArtifactPanel과 같은 preview provider registry 재사용

### Milestone 7: Hardening and share

- [ ] share snapshot과 artifact visibility 연결
- [ ] share artifact public read endpoint 테스트
- [ ] artifact 삭제/retention 정책 구현
- [ ] storage cleanup job 추가
- [ ] preview cache가 필요하면 별도 cache namespace 도입

### Milestone 8: Optional near-real-time

- [ ] skill subprocess 실행 중 output dir polling
- [ ] file update debounce
- [ ] writing/ready 상태 전환 event
- [ ] 대용량 파일 partial write 감지

### Milestone 9: Optional MinIO/S3

- [ ] S3 artifact storage backend 구현
- [ ] local/S3 storage integration test
- [ ] signed URL을 직접 노출할지 backend proxy를 유지할지 결정
- [ ] lifecycle policy와 bucket prefix cleanup 문서화

## 8. 테스트 계획

### Backend tests

- [ ] path traversal: `../`, absolute path, null byte, symlink 거부
- [ ] artifact ingest: created/updated/no-change delta
- [ ] same run + same logical path version 증가
- [ ] different run + same logical path는 별도 artifact 생성
- [ ] local storage object key가 LLM filename을 신뢰하지 않는지 확인
- [ ] router ownership: 타 사용자 conversation artifact 접근 거부
- [ ] library API: `q`, `agent_id`, `conversation_id`, `kind`, `favorite` 필터
- [ ] favorite toggle: 같은 사용자 artifact만 변경 가능
- [ ] stats API: total count/bytes, kind breakdown, favorite count
- [ ] opened/download tracking: `last_opened_at`, `preview_count`, `download_count` 증가
- [ ] share token: 공유된 conversation artifact만 접근 가능
- [ ] stream: `execute_in_skill` 결과 후 `file_event` emit
- [ ] stream resume: `message_events`에서 `file_event` replay
- [ ] quota: max bytes/max files 초과 시 failed event 또는 ingest skip

### Frontend tests

- [ ] SSE `file_event`가 artifact store를 갱신
- [ ] conversation 진입 시 artifact list fetch와 SSE state merge
- [ ] right rail `artifacts` mode 전환
- [ ] provider registry priority와 fallback
- [ ] Markdown/Mermaid/code artifact preview
- [ ] unsupported file download fallback
- [ ] `/artifacts` library search/filter/favorite/stat UI
- [ ] ArtifactPanel과 Generated File Library가 같은 preview provider를 재사용
- [ ] 기존 inline Markdown/Mermaid/image rendering 유지

## 9. 주요 리스크와 완화

### 기존 file endpoint 권한

기존 `/api/conversations/{conversation_id}/files/{file_path}`가 artifact API와 병존하면 권한 모델이 갈라질 수 있다. 1차 작업에서 owner guard를 점검하고, 신규 UI는 artifact id 기반 API만 사용한다.

### 부분 파일과 대용량 파일

1차 구현은 tool 종료 후 delta 감지이므로 partial write 문제가 적다. near-real-time polling 단계에서는 size/mtime이 안정화된 뒤 `ready`로 전환하는 debounce가 필요하다.

### HTML/SVG preview

HTML과 SVG는 preview UX는 좋지만 XSS 리스크가 높다. HTML은 sandbox iframe으로 제한하고, SVG는 image inline을 기본 비활성화하거나 sanitize된 preview만 제공한다.

### Office/CAD preview 기대치

Office, PPT, CAD preview는 파일 타입별 dependency와 변환 서버가 필요하다. 1차 통합 범위에서는 provider registry와 fallback download/open을 마련하고, 고급 preview는 필요한 포맷부터 개별 provider로 붙인다.

### Storage migration

처음부터 MinIO를 필수로 만들면 배포/운영 범위가 커진다. Local backend로 시작하되 DB manifest와 storage interface를 분리해 S3 전환이 API 변경 없이 가능하게 한다.

## 10. 통합 1차 범위

1차 PR의 목표는 "생성된 파일이 우측 패널에 뜨고, 동시에 전역 Generated File Library에서 검색/필터/즐겨찾기/통계로 재사용 가능하다"까지로 잡는다. 이 기능은 MVP와 P2로 나누지 않는다. 파일 인덱스, 패널, 라이브러리 화면이 같은 `conversation_artifacts` source of truth를 공유해야 API와 DB를 두 번 갈아엎지 않는다.

포함:

- local artifact storage
- DB manifest/version
- library metadata: `agent_id`, `artifact_kind`, `is_favorite`, `last_opened_at`, `preview_count`, `download_count`
- `execute_in_skill` 종료 후 delta ingest
- SSE `file_event`
- conversation artifact list/content/download API
- global generated file library API: search/filter/favorite/recent/stats
- right rail ArtifactPanel
- `/artifacts` Generated File Library 화면
- Markdown, Mermaid, code/text, image preview
- fallback download

제외:

- MinIO/S3 실제 구현
- sandbox write proxy
- Office/CAD 고급 preview
- 외부 npm plugin runtime loading
- 파일 공동 편집

이 범위가 가장 Moldy답다. 현재 제한된 `execute_in_skill` 모델을 유지하면서도 사용자가 체감하는 artifact 경험을 패널과 라이브러리 양쪽에서 크게 개선하고, 나중에 sandbox나 MinIO가 필요해졌을 때 갈아엎지 않고 확장할 수 있다.

## 11. 소스 코드 기준 상세 구현 계약

이 섹션은 앞선 대화 맥락 없이 이 문서만 보고 구현할 수 있도록 실제 Moldy 소스 구조에 맞춘 변경 계약이다. 아래 경로와 함수명은 2026-06-05 기준 코드에서 확인한 현재 구조다.

### 11.1 현재 런타임 불변식

- `backend/app/routers/conversations.py`의 `_prepare_stream_context(conversation_id)`가 매 assistant turn마다 `run_id`, `EventBroker`, `persist_callback`, `trace_sink`, `msg_id_sink`, `error_sink`를 만든다.
- `run_id`는 `stream_agent_response()`에 전달되고, 내부에서 `msg_id = run_id`가 된다. SSE id는 `"{msg_id}-{seq}"` 형식이다.
- 같은 `run_id`는 `message_events.assistant_msg_id`로 저장된다. 따라서 artifact는 별도 `messages` FK가 아니라 `assistant_msg_id/run_id`를 안정적인 turn key로 삼는다.
- `stream_agent_response()`의 `emit(event, data)`는 한 번 호출되면 SSE, broker publish, trace sink append, partial DB persistence buffer append를 모두 처리한다. `file_event`는 반드시 이 `emit()`을 통해 발행한다.
- `execute_in_skill`은 `backend/app/agent_runtime/executor.py`의 `_create_skill_execute_tool(ctx)` 내부 closure다. 스킬 출력 디렉토리는 `SkillToolContext.output_dir`이고, `build_skill_runtime_context()`가 `data/conversations/{thread_id}`로 잡는다.
- `frontend/src/lib/chat/use-chat-runtime.ts`는 SSE event switch에서 `content_delta`, `tool_call_start`, `tool_call_result`, memory events, `interrupt`, `error`, `message_end`를 처리한다. `file_event`는 여기서 artifact store만 갱신해야 하며 assistant message content를 수정하지 않는다.

### 11.2 Backend file map

Create:

- `backend/app/models/conversation_artifact.py`  
  `ConversationArtifact`, `ArtifactVersion`, status/storage constants.
- `backend/app/schemas/artifact.py`  
  API response schema, file event payload schema, content response schema.
- `backend/app/services/artifact_paths.py`  
  logical path normalization, safe filename, MIME detection, symlink/path traversal guard.
- `backend/app/services/artifact_storage.py`  
  storage interface and local disk backend.
- `backend/app/services/artifact_service.py`  
  output dir snapshot/diff, DB manifest/version creation, library search/filter/stats, favorite/open/download counters, content/download helpers.
- `backend/app/routers/artifacts.py`  
  authenticated conversation artifact APIs and global generated file library APIs.
- `backend/app/routers/share_artifacts.py`  
  public share-token artifact APIs, or add these routes to existing `shares.py` if the file remains readable.
- `backend/tests/test_artifact_paths.py`
- `backend/tests/test_artifact_storage.py`
- `backend/tests/test_artifact_service.py`
- `backend/tests/test_artifacts_router.py`
- `backend/tests/test_artifact_library_router.py`
- `backend/tests/integration/test_artifact_streaming.py`

Modify:

- `backend/app/config.py`  
  Add artifact storage settings next to `conversation_output_dir` and `upload_dir`.
- `backend/app/models/__init__.py`  
  Import and export new artifact models.
- `backend/app/agent_runtime/event_names.py`  
  Add `FILE_EVENT`.
- `backend/app/agent_runtime/streaming.py`  
  Add artifact recorder protocol parameter and emit `file_event` after `execute_in_skill` results.
- `backend/app/agent_runtime/executor.py`  
  Pass artifact recorder through `execute_agent_stream`, `resume_agent_stream`, `_run_agent_stream`, and `stream_agent_response`.
- `backend/app/routers/conversations.py`  
  Build an artifact recorder for message send/resume/edit/regenerate streams. Fix existing `/files/{file_path}` ownership guard.
- `backend/app/main.py`  
  Include artifact routers.
- `backend/alembic/versions/<next>_add_conversation_artifacts.py`  
  Add tables/indexes/check constraints.

### 11.3 Frontend file map

Create:

- `frontend/src/lib/chat/artifact-types.ts`  
  UI-only helpers if the shared `lib/types` file becomes too large.
- `frontend/src/lib/api/artifacts.ts`  
  Fetch conversation artifacts, global library list, stats, favorite/open/download metadata.
- `frontend/src/lib/hooks/use-conversation-artifacts.ts`
- `frontend/src/lib/hooks/use-artifact-library.ts`
- `frontend/src/lib/stores/chat-artifacts.ts`
- `frontend/src/app/artifacts/page.tsx`
- `frontend/src/components/artifacts/artifact-library-content.tsx`
- `frontend/src/components/artifacts/artifact-library-filters.tsx`
- `frontend/src/components/artifacts/artifact-library-stats.tsx`
- `frontend/src/components/chat/right-rail/artifact-panel-content.tsx`
- `frontend/src/components/chat/artifacts/artifact-preview.tsx`
- `frontend/src/components/chat/artifacts/preview-registry.tsx`
- `frontend/src/components/chat/artifacts/providers/image-preview.tsx`
- `frontend/src/components/chat/artifacts/providers/media-preview.tsx`
- `frontend/src/components/chat/artifacts/providers/markdown-preview.tsx`
- `frontend/src/components/chat/artifacts/providers/mermaid-preview.tsx`
- `frontend/src/components/chat/artifacts/providers/code-preview.tsx`
- `frontend/src/components/chat/artifacts/providers/text-preview.tsx`
- `frontend/src/components/chat/artifacts/providers/fallback-preview.tsx`
- `frontend/src/lib/stores/__tests__/chat-artifacts.test.ts`
- `frontend/src/lib/hooks/__tests__/use-artifact-library.test.tsx`
- `frontend/src/components/chat/artifacts/__tests__/preview-registry.test.tsx`

Modify:

- `frontend/src/lib/types/index.ts`  
  Add artifact types and `file_event` SSE union.
- `frontend/src/lib/chat/use-chat-runtime.ts`  
  Update artifact store on `file_event`.
- `frontend/src/lib/stores/chat-right-rail.ts`
  Add `artifacts` mode.
- `frontend/src/components/chat/right-rail/chat-right-rail.tsx`
  Render `ArtifactPanelContent`.
- `frontend/src/components/layout/app-sidebar.tsx`
  Add a first-class Files/Artifacts nav item pointing to `/artifacts`.
- `frontend/src/components/layout/breadcrumb-nav.tsx`
  Add breadcrumb label for `/artifacts`.
- `frontend/messages/ko.json`, `frontend/messages/en.json`
  Add all visible copy.

### 11.4 Backend model skeleton

Use JSON columns rather than JSONB in SQLAlchemy model code unless existing migration uses PostgreSQL-specific JSONB explicitly. Existing `MessageEvent` uses `JSON`, and backend tests run on aiosqlite.

```python
# backend/app/models/conversation_artifact.py
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base

ARTIFACT_STATUS_VALUES = ("writing", "ready", "deleted", "failed")
ARTIFACT_STORAGE_VALUES = ("local", "s3")
ARTIFACT_KIND_VALUES = (
    "image",
    "video",
    "audio",
    "pdf",
    "markdown",
    "html",
    "code",
    "document",
    "data",
    "cad",
    "other",
)


class ConversationArtifact(Base):
    __tablename__ = "conversation_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "assistant_msg_id",
            "logical_path",
            name="uq_conversation_artifacts_turn_path",
        ),
        Index("ix_conversation_artifacts_user_conversation_created", "user_id", "conversation_id", "created_at"),
        Index("ix_conversation_artifacts_conversation_turn_updated", "conversation_id", "assistant_msg_id", "updated_at"),
        Index("ix_conversation_artifacts_user_created", "user_id", "created_at"),
        Index("ix_conversation_artifacts_user_agent_created", "user_id", "agent_id", "created_at"),
        Index("ix_conversation_artifacts_user_kind_created", "user_id", "artifact_kind", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    assistant_msg_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_call_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_tool_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    logical_path: Mapped[str] = mapped_column(String(500), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str | None] = mapped_column(String(40), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    artifact_kind: Mapped[str] = mapped_column(String(30), nullable=False, default="other")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ready")
    is_favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    preview_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    download_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    branch_checkpoint_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    linked_message_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
    )


class ArtifactVersion(Base):
    __tablename__ = "artifact_versions"
    __table_args__ = (
        UniqueConstraint("artifact_id", "version_number", name="uq_artifact_versions_number"),
        Index("ix_artifact_versions_artifact_created", "artifact_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation_artifacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_provider: Mapped[str] = mapped_column(String(20), nullable=False, default="local")
    bucket: Mapped[str | None] = mapped_column(String(255), nullable=True)
    object_key: Mapped[str] = mapped_column(String(800), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC).replace(tzinfo=None),
        nullable=False,
    )
```

PostgreSQL migration should add `CHECK (status IN (...))`, `CHECK (artifact_kind IN (...))`, `CHECK (storage_provider IN (...))`, and a partial favorite index for `is_favorite = true`. SQLite tests can rely on SQLAlchemy model plus service validation.

### 11.5 Config additions

Add below upload settings in `backend/app/config.py`.

```python
    # Agent/runtime generated artifacts. The first integrated release uses local disk; S3/MinIO can be
    # added behind app.services.artifact_storage without changing API URLs.
    artifact_storage_backend: Literal["local", "s3"] = "local"
    artifact_storage_dir: str = "./data/artifacts"
    artifact_max_bytes: int = 100 * 1024 * 1024
    artifact_max_files_per_run: int = 100
    artifact_preview_max_text_bytes: int = 1 * 1024 * 1024
    artifact_s3_endpoint_url: str = ""
    artifact_s3_bucket: str = ""
    artifact_s3_access_key_id: str = ""
    artifact_s3_secret_access_key: str = ""
```

`Literal` is already imported at the top of `config.py`.

### 11.6 Path utility contract

`backend/app/services/artifact_paths.py` should own all path validation. Do not duplicate traversal checks in routers/services.

```python
from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path


class ArtifactPathError(ValueError):
    pass


_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")
_SKIP_NAMES = {".DS_Store"}
_SKIP_PARTS = {"__pycache__", ".previews"}


@dataclass(frozen=True)
class NormalizedArtifactPath:
    logical_path: str
    display_name: str
    extension: str | None
    mime_type: str
    artifact_kind: str


def artifact_kind_for(mime_type: str, extension: str | None) -> str:
    ext = (extension or "").lower()
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type == "application/pdf" or ext == "pdf":
        return "pdf"
    if mime_type == "text/markdown" or ext in {"md", "markdown", "mmd", "mermaid"}:
        return "markdown"
    if mime_type == "text/html" or ext in {"html", "htm"}:
        return "html"
    if ext in {"py", "js", "ts", "tsx", "jsx", "css", "json", "yaml", "yml", "toml", "sql", "sh"}:
        return "code"
    if ext in {"csv", "tsv", "xlsx", "xls"}:
        return "data"
    if ext in {"doc", "docx", "ppt", "pptx"}:
        return "document"
    if ext in {"dwg", "dxf", "step", "stp", "iges", "igs", "stl"}:
        return "cad"
    return "other"


def normalize_output_path(base_dir: Path, path: Path) -> NormalizedArtifactPath:
    resolved_base = base_dir.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_base):
        raise ArtifactPathError("artifact path escapes output directory")
    if not resolved_path.is_file():
        raise ArtifactPathError("artifact path is not a file")
    if path.is_symlink() or resolved_path.is_symlink():
        raise ArtifactPathError("artifact symlinks are not allowed")

    relative = resolved_path.relative_to(resolved_base)
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ArtifactPathError("artifact path contains invalid segments")
    if any(part in _SKIP_PARTS for part in parts) or relative.name in _SKIP_NAMES:
        raise ArtifactPathError("artifact path is excluded")
    if len(parts) > 12:
        raise ArtifactPathError("artifact path is too deep")
    if any(len(part) > 120 for part in parts):
        raise ArtifactPathError("artifact path segment is too long")

    logical_path = relative.as_posix()
    if len(logical_path) > 500:
        raise ArtifactPathError("artifact path is too long")
    if any(ord(ch) < 32 for ch in logical_path):
        raise ArtifactPathError("artifact path contains control characters")

    extension = relative.suffix.lower().lstrip(".") or None
    mime_type = mimetypes.guess_type(relative.name)[0] or "application/octet-stream"
    artifact_kind = artifact_kind_for(mime_type, extension)
    return NormalizedArtifactPath(
        logical_path=logical_path,
        display_name=relative.name,
        extension=extension,
        mime_type=mime_type,
        artifact_kind=artifact_kind,
    )


def safe_storage_filename(display_name: str) -> str:
    cleaned = _SAFE_FILENAME_RE.sub("_", display_name).strip(" .")
    return cleaned[:160] or "artifact"
```

### 11.7 Storage interface contract

The first integrated local backend writes canonical copies to `settings.artifact_storage_dir`. Routers should never serve directly from `data/conversations/{conversation_id}` once an artifact row exists.

```python
# backend/app/services/artifact_storage.py
from __future__ import annotations

import asyncio
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.config import settings
from app.services.artifact_paths import safe_storage_filename


@dataclass(frozen=True)
class StoredArtifactObject:
    storage_provider: str
    bucket: str | None
    object_key: str
    path: Path | None


class ArtifactStorageBackend(Protocol):
    async def put_file(
        self,
        *,
        conversation_id: uuid.UUID,
        artifact_id: uuid.UUID,
        version_number: int,
        display_name: str,
        source_path: Path,
    ) -> StoredArtifactObject:
        ...

    async def local_path(self, *, object_key: str) -> Path:
        ...


class LocalArtifactStorageBackend:
    provider = "local"

    def __init__(self, root_dir: str | Path | None = None) -> None:
        self.root_dir = Path(root_dir or settings.artifact_storage_dir)

    async def put_file(
        self,
        *,
        conversation_id: uuid.UUID,
        artifact_id: uuid.UUID,
        version_number: int,
        display_name: str,
        source_path: Path,
    ) -> StoredArtifactObject:
        safe_name = safe_storage_filename(display_name)
        object_key = (
            f"conversations/{conversation_id}/{artifact_id}/"
            f"v{version_number}/{safe_name}"
        )
        target = (self.root_dir / object_key).resolve()
        root = self.root_dir.resolve()
        if not target.is_relative_to(root):
            raise ValueError("artifact object key escapes storage root")
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, source_path, target)
        return StoredArtifactObject(
            storage_provider=self.provider,
            bucket=None,
            object_key=object_key,
            path=target,
        )

    async def local_path(self, *, object_key: str) -> Path:
        target = (self.root_dir / object_key).resolve()
        if not target.is_relative_to(self.root_dir.resolve()):
            raise ValueError("artifact object key escapes storage root")
        return target
```

### 11.8 Artifact service and stream recorder contract

The streaming layer should not know SQLAlchemy table details. It receives a recorder object with a tiny async method. This preserves `streaming.py` as event orchestration code rather than storage code.

```python
# backend/app/services/artifact_service.py
from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.services.artifact_paths import ArtifactPathError, NormalizedArtifactPath, normalize_output_path
from app.services.artifact_storage import ArtifactStorageBackend, LocalArtifactStorageBackend


@dataclass(frozen=True)
class ArtifactFileState:
    logical_path: str
    path: Path
    size_bytes: int
    mtime_ns: int
    sha256: str
    normalized: NormalizedArtifactPath


@dataclass
class ArtifactSnapshot:
    files: dict[str, ArtifactFileState] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactRuntimeContext:
    conversation_id: uuid.UUID
    user_id: uuid.UUID
    agent_id: uuid.UUID
    assistant_msg_id: str
    output_dir: Path
    source_tool_name: str = "execute_in_skill"
    branch_checkpoint_id: str | None = None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def snapshot_output_dir(base_dir: Path) -> ArtifactSnapshot:
    def _scan() -> ArtifactSnapshot:
        files: dict[str, ArtifactFileState] = {}
        if not base_dir.exists():
            return ArtifactSnapshot(files=files)
        for path in base_dir.rglob("*"):
            try:
                normalized = normalize_output_path(base_dir, path)
            except ArtifactPathError:
                continue
            stat = path.stat()
            files[normalized.logical_path] = ArtifactFileState(
                logical_path=normalized.logical_path,
                path=path,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                sha256=_sha256_file(path),
                normalized=normalized,
            )
        return ArtifactSnapshot(files=files)

    return await asyncio.to_thread(_scan)


def diff_snapshots(before: ArtifactSnapshot, after: ArtifactSnapshot) -> list[ArtifactFileState]:
    changed: list[ArtifactFileState] = []
    for logical_path, current in after.files.items():
        previous = before.files.get(logical_path)
        if previous is None or previous.sha256 != current.sha256:
            changed.append(current)
    return changed


class ArtifactDeltaRecorder:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        context: ArtifactRuntimeContext,
        storage: ArtifactStorageBackend | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._context = context
        self._storage = storage or LocalArtifactStorageBackend()
        self._snapshot = ArtifactSnapshot()
        self._prepared = False

    async def prepare(self) -> None:
        self._snapshot = await snapshot_output_dir(self._context.output_dir)
        self._prepared = True

    async def collect_after_tool_result(
        self,
        *,
        tool_name: str,
        tool_call_id: str | None,
    ) -> list[dict[str, Any]]:
        if tool_name != self._context.source_tool_name:
            return []
        if not self._prepared:
            await self.prepare()
        after = await snapshot_output_dir(self._context.output_dir)
        changed = diff_snapshots(self._snapshot, after)
        self._snapshot = after
        if not changed:
            return []
        async with self._session_factory() as session:
            payloads = await ingest_changed_files(
                session,
                context=self._context,
                changed=changed,
                storage=self._storage,
                tool_call_id=tool_call_id,
            )
            await session.commit()
        return payloads
```

`ingest_changed_files()` must upsert by `(conversation_id, assistant_msg_id, logical_path)`. If an artifact exists and `sha256` changed, create `ArtifactVersion(version_number=max+1)`, update `current_version_id`, `sha256`, `size_bytes`, `artifact_kind`, `updated_at`, and emit `op="updated"`. If not exists, create artifact + version with `user_id`, `agent_id`, `conversation_id`, `assistant_msg_id`, `logical_path`, `artifact_kind`, and emit `op="created"`.

Payload builder should produce the exact event schema in section 5.4. `url`, `preview_url`, `download_url` should use artifact id endpoints, not the legacy `/files/{file_path}` endpoint.

### 11.9 Streaming integration contract

In `backend/app/agent_runtime/streaming.py`, add a Protocol near the top-level type aliases.

```python
from typing import Protocol


class ArtifactDeltaRecorderProtocol(Protocol):
    async def prepare(self) -> None:
        ...

    async def collect_after_tool_result(
        self,
        *,
        tool_name: str,
        tool_call_id: str | None,
    ) -> list[dict[str, Any]]:
        ...
```

Add a parameter to `stream_agent_response()`:

```python
    artifact_recorder: ArtifactDeltaRecorderProtocol | None = None,
```

After `yield emit(event_names.MESSAGE_START, start_data)` and before `agent.astream(...)`, prepare the recorder. If prepare fails, emit an `error` only if the stream cannot proceed. Recommended 1차 behavior is fail-open with a log, because artifact panel/library failure must not block chat.

```python
    yield emit(event_names.MESSAGE_START, start_data)
    if artifact_recorder is not None:
        try:
            await artifact_recorder.prepare()
        except Exception:
            logger.warning("artifact recorder prepare failed (run_id=%s)", msg_id, exc_info=True)
            artifact_recorder = None
```

Inside the current `if msg.type == "tool":` block, immediately after `yield emit(event_names.TOOL_CALL_RESULT, result_payload)`, emit artifact events for `execute_in_skill`.

```python
                        yield emit(event_names.TOOL_CALL_RESULT, result_payload)
                        if artifact_recorder is not None:
                            try:
                                file_payloads = await artifact_recorder.collect_after_tool_result(
                                    tool_name=tool_name,
                                    tool_call_id=tool_call_id if isinstance(tool_call_id, str) else None,
                                )
                            except Exception:
                                logger.warning(
                                    "artifact delta collection failed (run_id=%s tool=%s)",
                                    msg_id,
                                    tool_name,
                                    exc_info=True,
                                )
                                file_payloads = []
                            for file_payload in file_payloads:
                                yield emit(event_names.FILE_EVENT, file_payload)
```

This preserves the event order:

```text
tool_call_result
file_event created/updated...
message_end
```

The frontend can therefore show tool stdout/stderr and artifact list independently.

### 11.10 Executor pass-through contract

In `backend/app/agent_runtime/executor.py`, add `artifact_recorder: Any | None = None` to the following functions and pass it through unchanged:

- `_run_agent_stream(...)`
- `execute_agent_stream(...)`
- `resume_agent_stream(...)`
- `run_agent(...)` only if its stream path delegates to the same function and tests require signature consistency.

At the existing call to `stream_agent_response(...)`, add:

```python
                artifact_recorder=artifact_recorder,
```

Do not put artifact detection inside `_create_skill_execute_tool()` for the first integrated release. That closure currently validates skill slug, prepares env, runs subprocess, redacts credentials, and appends `OUTPUT_FILES`. Keeping artifact ingest outside the tool avoids DB/session coupling inside tool execution.

### 11.11 Router integration contract

Add a helper in `backend/app/routers/conversations.py`.

```python
def _build_artifact_recorder(
    *,
    conversation_id: uuid.UUID,
    user: CurrentUser,
    agent_id: uuid.UUID,
    run_id: str,
) -> ArtifactDeltaRecorder:
    return ArtifactDeltaRecorder(
        session_factory=async_session,
        context=ArtifactRuntimeContext(
            conversation_id=conversation_id,
            user_id=user.id,
            agent_id=agent_id,
            assistant_msg_id=run_id,
            output_dir=Path(settings.conversation_output_dir) / str(conversation_id),
        ),
    )
```

Required imports:

```python
from pathlib import Path
from app.config import settings
from app.services.artifact_service import ArtifactDeltaRecorder, ArtifactRuntimeContext
```

Use the helper in every route that starts an assistant stream:

- `send_message`
- `resume_message`
- `edit_message`
- `regenerate_message`

Pattern:

```python
    ctx = _prepare_stream_context(conversation_id)
    artifact_recorder = _build_artifact_recorder(
        conversation_id=conversation_id,
        user=user,
        agent_id=uuid.UUID(cfg.agent_id),
        run_id=ctx.run_id,
    )
    return _sse_handler(
        lambda: execute_agent_stream(
            cfg,
            [{"role": "user", "content": data.content}],
            moldy_source="chat",
            artifact_recorder=artifact_recorder,
            **ctx.as_stream_kwargs(),
        ),
        ...
    )
```

For `resume_agent_stream(...)`, pass the same `artifact_recorder=artifact_recorder`.

Fix the existing file endpoint at the same time. Current code calls `chat_service.get_conversation(db, conversation_id)` even though it receives `CurrentUser`. Change it to owner-gated lookup:

```python
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if not conv:
        raise conversation_not_found()
```

This is required even if the new UI stops using `/files/{file_path}` because old Markdown links can still hit that endpoint.

### 11.12 Artifact router contract

`backend/app/routers/artifacts.py` should use owner-gated conversation lookup for every route.

```python
@router.get("/api/conversations/{conversation_id}/artifacts")
async def list_conversation_artifacts(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[ArtifactSummary]:
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if not conv:
        raise conversation_not_found()
    return await artifact_service.list_artifacts(db, conversation_id=conversation_id)
```

For content route:

- Text-like MIME (`text/*`, `application/json`, `application/xml`, `application/csv`) returns UTF-8 text with replacement and a `truncated` flag if over `settings.artifact_preview_max_text_bytes`.
- Binary files return `415` for `/content` unless a provider-specific preview exists.
- Download route streams with `FileResponse` for local backend and backend-proxy streaming for S3 later.

`download` must use `filename=artifact.display_name` and `media_type=artifact.mime_type`.

Global library routes in the same router should use artifact-owner lookup, not conversation lookup. The service query must always include `ConversationArtifact.user_id == user.id`.

```python
@router.get("/api/artifacts")
async def list_generated_artifacts(
    q: str | None = Query(None),
    agent_id: uuid.UUID | None = Query(None),
    conversation_id: uuid.UUID | None = Query(None),
    kind: str | None = Query(None),
    favorite: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> ArtifactLibraryPage:
    return await artifact_service.list_library_artifacts(
        db,
        user_id=user.id,
        q=q,
        agent_id=agent_id,
        conversation_id=conversation_id,
        kind=kind,
        favorite=favorite,
        limit=limit,
        cursor=cursor,
    )
```

Required service functions:

```text
list_library_artifacts(user_id, q, agent_id, conversation_id, kind, favorite, limit, cursor)
get_library_stats(user_id)
set_artifact_favorite(user_id, artifact_id, is_favorite)
record_artifact_opened(user_id, artifact_id)
record_artifact_download(user_id, artifact_id)
```

`content` and `download` endpoints can be exposed under both `/api/conversations/{id}/artifacts/{artifact_id}/...` and `/api/artifacts/{artifact_id}/...`. Both must call the same service method that validates `artifact.user_id == user.id`. The conversation-scoped variant additionally verifies the artifact belongs to that conversation.

### 11.13 Frontend type contract

Add to `frontend/src/lib/types/index.ts`.

```ts
export type ArtifactStatus = 'writing' | 'ready' | 'deleted' | 'failed'
export type ArtifactKind =
  | 'image'
  | 'video'
  | 'audio'
  | 'pdf'
  | 'markdown'
  | 'html'
  | 'code'
  | 'document'
  | 'data'
  | 'cad'
  | 'other'
export type FileEventOperation = 'created' | 'updated' | 'deleted' | 'failed'

export interface ArtifactSummary {
  id: string
  agent_id: string
  conversation_id: string
  assistant_msg_id: string
  run_id: string
  tool_call_id?: string | null
  source_tool_name?: string | null
  path: string
  display_name: string
  mime_type: string
  extension?: string | null
  artifact_kind: ArtifactKind
  size_bytes: number
  sha256: string
  status: ArtifactStatus
  is_favorite: boolean
  last_opened_at?: string | null
  preview_count: number
  download_count: number
  version_id: string
  version_number: number
  created_at: string
  updated_at: string
  agent_name?: string | null
  conversation_title?: string | null
  url: string
  preview_url: string
  download_url: string
}

export interface FileEventPayload extends ArtifactSummary {
  op: FileEventOperation
}

export interface ArtifactLibraryPage {
  items: ArtifactSummary[]
  next_cursor: string | null
  has_more: boolean
}

export interface ArtifactLibraryParams {
  q?: string
  agent_id?: string
  conversation_id?: string
  kind?: ArtifactKind
  favorite?: boolean
  limit?: number
  cursor?: string | null
}

export interface ArtifactKindStat {
  kind: ArtifactKind
  count: number
  size_bytes: number
}

export interface ArtifactLibraryStats {
  total_count: number
  total_size_bytes: number
  favorite_count: number
  by_kind: ArtifactKindStat[]
  recent_count_7d: number
}
```

Update `SSEEventType`:

```ts
  | 'file_event'
```

Update `SSEEvent` union:

```ts
  | { event: 'file_event'; data: FileEventPayload }
```

### 11.14 Frontend store contract

Create `frontend/src/lib/stores/chat-artifacts.ts`.

```ts
import { atom } from 'jotai'
import type { ArtifactSummary, FileEventPayload } from '@/lib/types'

export type ArtifactMap = Record<string, ArtifactSummary[]>

export const chatArtifactsAtom = atom<ArtifactMap>({})

export const upsertChatArtifactAtom = atom(null, (get, set, payload: FileEventPayload) => {
  const current = get(chatArtifactsAtom)
  const conversationId = payload.conversation_id
  const existing = current[conversationId] ?? []
  const withoutDeleted =
    payload.op === 'deleted'
      ? existing.filter((artifact) => artifact.id !== payload.id)
      : existing
  if (payload.op === 'deleted') {
    set(chatArtifactsAtom, { ...current, [conversationId]: withoutDeleted })
    return
  }
  const idx = withoutDeleted.findIndex((artifact) => artifact.id === payload.id)
  const nextItem: ArtifactSummary = payload
  const next =
    idx >= 0
      ? withoutDeleted.map((artifact, index) => (index === idx ? nextItem : artifact))
      : [nextItem, ...withoutDeleted]
  set(chatArtifactsAtom, { ...current, [conversationId]: next })
})

export const replaceConversationArtifactsAtom = atom(
  null,
  (get, set, update: { conversationId: string; artifacts: ArtifactSummary[] }) => {
    const current = get(chatArtifactsAtom)
    set(chatArtifactsAtom, { ...current, [update.conversationId]: update.artifacts })
  },
)
```

`use-chat-runtime.ts` currently uses mocked `useSetAtom` in tests. Add:

```ts
import { upsertChatArtifactAtom } from '@/lib/stores/chat-artifacts'
```

Inside `useChatRuntime`, create:

```ts
const upsertChatArtifact = useSetAtom(upsertChatArtifactAtom)
```

Add switch case:

```ts
            case 'file_event': {
              upsertChatArtifact(event.data)
              break
            }
```

This case should not call `setStreamingMessages()` because artifacts are independent right-rail state.

### 11.15 Right rail contract

Update `frontend/src/lib/stores/chat-right-rail.ts`.

```ts
export type RightRailMode = 'none' | 'subagent' | 'tool-result' | 'outline' | 'artifacts'

export interface ArtifactsPayload {
  conversationId: string
  selectedArtifactId?: string | null
}

export type RightRailState =
  | { mode: 'none' }
  | { mode: 'subagent'; subagent: SubagentPayload }
  | { mode: 'tool-result'; toolResult: ToolResultPayload }
  | { mode: 'outline'; outline: OutlinePayload }
  | { mode: 'artifacts'; artifacts: ArtifactsPayload }
```

Update `conversationIdForState()` and `titleFor()` in `chat-right-rail.tsx`:

```ts
  if (state.mode === 'artifacts') return state.artifacts.conversationId
```

```ts
  if (state.mode === 'artifacts') return t('artifacts')
```

Because `titleFor` currently does not receive `t`, either change the signature to `titleFor(state, t)` or keep a literal only if moved into `messages`. Product copy must go through `next-intl`, so prefer `titleFor(state, t)`.

Render:

```tsx
{state.mode === 'artifacts' ? <ArtifactPanelContent payload={state.artifacts} /> : null}
```

### 11.16 Preview registry contract

The registry should be deterministic and small.

```tsx
// frontend/src/components/chat/artifacts/preview-registry.tsx
import type { ArtifactSummary } from '@/lib/types'

export interface ArtifactPreviewProps {
  artifact: ArtifactSummary
}

export interface ArtifactPreviewProvider {
  id: string
  priority: number
  match: (artifact: ArtifactSummary) => boolean
  Component: React.ComponentType<ArtifactPreviewProps>
}

export function pickPreviewProvider(
  artifact: ArtifactSummary,
  providers: ArtifactPreviewProvider[],
): ArtifactPreviewProvider {
  return [...providers]
    .sort((a, b) => b.priority - a.priority)
    .find((provider) => provider.match(artifact)) ?? fallbackPreviewProvider
}
```

Provider matching rules for the first integrated release:

- Image: `artifact.mime_type.startsWith('image/')`, except SVG unless sanitized policy is implemented.
- Audio/video: `audio/*`, `video/*`.
- Markdown: extension `md`, `markdown`, MIME `text/markdown`.
- Mermaid: extension `mmd`, `mermaid`, or display name ending `.mermaid`.
- Code/text/json/csv: text-like MIME or known code extension.
- Fallback: all files.

Markdown provider may reuse `MarkdownContent`, but set `isStreaming={false}`. Mermaid provider may reuse `MermaidDiagram` directly for `.mmd` files.

## 12. Task-by-task implementation plan

Each task should be implemented and committed separately. Commands assume the repository root is `/Users/chester/dev/ref/natural-mold`.

### Task 1: Artifact DB model and migration

**Files:**

- Create: `backend/app/models/conversation_artifact.py`
- Create: `backend/alembic/versions/<next>_add_conversation_artifacts.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_artifact_models.py`

- [ ] **Step 1: Write model import test**

```python
# backend/tests/test_artifact_models.py
from app.models import ArtifactVersion, ConversationArtifact


def test_artifact_models_are_registered() -> None:
    assert ConversationArtifact.__tablename__ == "conversation_artifacts"
    assert ArtifactVersion.__tablename__ == "artifact_versions"
```

- [ ] **Step 2: Run the failing test**

```bash
cd backend
uv run pytest tests/test_artifact_models.py -q
```

Expected: import failure because the models do not exist yet.

- [ ] **Step 3: Add model classes**

Use the complete model skeleton in section 11.4. Then add imports/exports in `backend/app/models/__init__.py`:

```python
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
```

and include `"ArtifactVersion"` and `"ConversationArtifact"` in `__all__`.

- [ ] **Step 4: Add Alembic migration**

Migration must create both tables, indexes, unique constraints, and PostgreSQL CHECK constraints for status/storage provider values. Use `sa.JSON()` for `metadata_json` and `linked_message_ids` to keep tests dialect-friendly.

- [ ] **Step 5: Run the model test**

```bash
cd backend
uv run pytest tests/test_artifact_models.py -q
```

Expected: pass.

### Task 2: Artifact path and storage services

**Files:**

- Create: `backend/app/services/artifact_paths.py`
- Create: `backend/app/services/artifact_storage.py`
- Test: `backend/tests/test_artifact_paths.py`
- Test: `backend/tests/test_artifact_storage.py`

- [ ] **Step 1: Write path tests**

```python
# backend/tests/test_artifact_paths.py
from pathlib import Path

import pytest

from app.services.artifact_paths import ArtifactPathError, normalize_output_path, safe_storage_filename


def test_normalize_output_path_accepts_nested_file(tmp_path: Path) -> None:
    base = tmp_path / "outputs"
    target = base / "report" / "final.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Final", encoding="utf-8")

    normalized = normalize_output_path(base, target)

    assert normalized.logical_path == "report/final.md"
    assert normalized.display_name == "final.md"
    assert normalized.extension == "md"
    assert normalized.mime_type in {"text/markdown", "text/x-markdown", "text/plain"}


def test_normalize_output_path_rejects_escape(tmp_path: Path) -> None:
    base = tmp_path / "outputs"
    base.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ArtifactPathError):
        normalize_output_path(base, outside)


def test_safe_storage_filename_removes_control_and_slashes() -> None:
    assert safe_storage_filename("../weird:name.md") == "_weird_name.md"
```

- [ ] **Step 2: Run path tests and confirm failure**

```bash
cd backend
uv run pytest tests/test_artifact_paths.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement `artifact_paths.py`**

Use section 11.6 exactly, adjusting MIME assertion in tests only if Python's `mimetypes` differs on the local machine.

- [ ] **Step 4: Write storage tests**

```python
# backend/tests/test_artifact_storage.py
from pathlib import Path
import uuid

import pytest

from app.services.artifact_storage import LocalArtifactStorageBackend


@pytest.mark.asyncio
async def test_local_storage_copies_file_under_artifact_root(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("hello", encoding="utf-8")
    storage = LocalArtifactStorageBackend(tmp_path / "artifacts")
    conversation_id = uuid.uuid4()
    artifact_id = uuid.uuid4()

    stored = await storage.put_file(
        conversation_id=conversation_id,
        artifact_id=artifact_id,
        version_number=1,
        display_name="final.md",
        source_path=source,
    )

    assert stored.storage_provider == "local"
    assert stored.bucket is None
    assert stored.object_key.endswith("/v1/final.md")
    assert stored.path is not None
    assert stored.path.read_text(encoding="utf-8") == "hello"
```

- [ ] **Step 5: Implement `artifact_storage.py` and run tests**

```bash
cd backend
uv run pytest tests/test_artifact_paths.py tests/test_artifact_storage.py -q
```

Expected: pass.

### Task 3: Artifact service ingest and event payloads

**Files:**

- Create: `backend/app/schemas/artifact.py`
- Create: `backend/app/services/artifact_service.py`
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_artifact_service.py`

- [ ] **Step 1: Add config settings**

Add the config block from section 11.5.

- [ ] **Step 2: Write service ingest test**

```python
# backend/tests/test_artifact_service.py
from pathlib import Path
import uuid

import pytest
from sqlalchemy import select

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion, ConversationArtifact
from app.models.model import Model
from app.models.user import User
from app.services.artifact_service import (
    ArtifactDeltaRecorder,
    ArtifactRuntimeContext,
)
from app.services.artifact_storage import LocalArtifactStorageBackend
from tests.conftest import TEST_USER_ID, TestSession


async def _seed_conversation() -> tuple[uuid.UUID, uuid.UUID]:
    async with TestSession() as db:
        if await db.get(User, TEST_USER_ID) is None:
            db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test"))
        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()
        agent = Agent(
            user_id=TEST_USER_ID,
            name="Artifact Tester",
            description=None,
            system_prompt="...",
            model_id=model.id,
            status="active",
        )
        db.add(agent)
        await db.flush()
        conv = Conversation(agent_id=agent.id, title="Artifacts")
        db.add(conv)
        await db.commit()
        return conv.id, agent.id


@pytest.mark.asyncio
async def test_recorder_ingests_created_and_updated_file(tmp_path: Path) -> None:
    conv_id, agent_id = await _seed_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    storage = LocalArtifactStorageBackend(tmp_path / "artifacts")
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-1",
            output_dir=output_dir,
        ),
        storage=storage,
    )

    await recorder.prepare()
    report = output_dir / "report.md"
    report.write_text("v1", encoding="utf-8")
    created = await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-1",
    )

    assert [event["op"] for event in created] == ["created"]
    artifact_id = created[0]["id"]

    report.write_text("v2", encoding="utf-8")
    updated = await recorder.collect_after_tool_result(
        tool_name="execute_in_skill",
        tool_call_id="call-1",
    )

    assert [event["op"] for event in updated] == ["updated"]
    assert updated[0]["id"] == artifact_id

    async with TestSession() as db:
        artifacts = (await db.execute(select(ConversationArtifact))).scalars().all()
        versions = (await db.execute(select(ArtifactVersion))).scalars().all()
        assert len(artifacts) == 1
        assert len(versions) == 2
        assert artifacts[0].logical_path == "report.md"
        assert artifacts[0].current_version_id == versions[-1].id
```

- [ ] **Step 3: Run the failing service test**

```bash
cd backend
uv run pytest tests/test_artifact_service.py -q
```

Expected: imports fail until service/schema exist.

- [ ] **Step 4: Implement service and schema**

Implement section 11.8 plus Pydantic response models. Event payload keys must match section 11.13 exactly: `id`, `conversation_id`, `assistant_msg_id`, `run_id`, `path`, `display_name`, `mime_type`, `extension`, `size_bytes`, `sha256`, `status`, `version_id`, `version_number`, `url`, `preview_url`, `download_url`, and `op`.

- [ ] **Step 5: Run service tests**

```bash
cd backend
uv run pytest tests/test_artifact_service.py -q
```

Expected: pass.

### Task 4: SSE file_event integration

**Files:**

- Modify: `backend/app/agent_runtime/event_names.py`
- Modify: `backend/app/agent_runtime/streaming.py`
- Modify: `backend/app/agent_runtime/executor.py`
- Modify: `backend/app/routers/conversations.py`
- Test: `backend/tests/integration/test_artifact_streaming.py`

- [ ] **Step 1: Write streaming integration test**

```python
# backend/tests/integration/test_artifact_streaming.py
from pathlib import Path
import uuid

import pytest

from app.agent_runtime.streaming import stream_agent_response
from app.services.artifact_service import ArtifactDeltaRecorder, ArtifactRuntimeContext
from app.services.artifact_storage import LocalArtifactStorageBackend
from tests.conftest import TEST_USER_ID, TestSession
from tests.test_artifact_service import _seed_conversation


class FakeAgent:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    async def astream(self, input_, config, stream_mode):
        from langchain_core.messages import ToolMessage
        target = self.output_dir / "chart.csv"
        target.write_text("x,y\n1,2\n", encoding="utf-8")
        yield ToolMessage(
            content="created chart",
            name="execute_in_skill",
            tool_call_id="call-1",
        ), {}

    async def aget_state(self, config):
        class State:
            tasks = []

        return State()


@pytest.mark.asyncio
async def test_stream_emits_file_event_after_execute_in_skill(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    conv_id, agent_id = await _seed_conversation()
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-artifact-1",
            output_dir=output_dir,
        ),
        storage=LocalArtifactStorageBackend(tmp_path / "artifacts"),
    )

    chunks = [
        chunk
        async for chunk in stream_agent_response(
            FakeAgent(output_dir),
            [{"role": "user", "content": "make csv"}],
            config={"configurable": {"thread_id": str(conv_id)}},
            run_id="run-artifact-1",
            artifact_recorder=recorder,
        )
    ]

    body = "".join(chunks)
    assert "event: tool_call_result" in body
    assert "event: file_event" in body
    assert '"path":"chart.csv"' in body or '"path": "chart.csv"' in body
```

This test may need a seeded conversation row if `ingest_changed_files()` enforces FK existence. If so, reuse `_seed_conversation()` from Task 3 instead of `uuid.uuid4()`.

- [ ] **Step 2: Run failing integration test**

```bash
cd backend
uv run pytest tests/integration/test_artifact_streaming.py -q
```

Expected: `artifact_recorder` parameter is not accepted or `file_event` constant is missing.

- [ ] **Step 3: Add `FILE_EVENT` constant**

```python
# backend/app/agent_runtime/event_names.py
FILE_EVENT: Final = "file_event"
```

- [ ] **Step 4: Implement streaming/executor/router pass-through**

Apply sections 11.9, 11.10, and 11.11. Keep `OUTPUT_FILES` in `execute_in_skill` unchanged.

- [ ] **Step 5: Run streaming tests**

```bash
cd backend
uv run pytest tests/integration/test_artifact_streaming.py tests/integration/test_broker_dual_write.py tests/integration/test_stream_resume.py -q
```

Expected: pass. `test_stream_resume.py` is included because `file_event` must not break DB replay slicing.

### Task 5: Artifact APIs and existing file endpoint guard

**Files:**

- Create: `backend/app/routers/artifacts.py`
- Modify: `backend/app/routers/conversations.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_artifacts_router.py`
- Test: `backend/tests/test_artifact_library_router.py`

- [ ] **Step 1: Write router tests**

```python
# backend/tests/test_artifacts_router.py
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.services.artifact_service import ArtifactDeltaRecorder, ArtifactRuntimeContext
from app.services.artifact_storage import LocalArtifactStorageBackend
from tests.conftest import TEST_USER_ID, TestSession
from tests.test_artifact_service import _seed_conversation


@pytest.mark.asyncio
async def test_artifact_list_and_download(client: AsyncClient, tmp_path: Path) -> None:
    conv_id, agent_id = await _seed_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / "report.md").write_text("# Report", encoding="utf-8")
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-api-1",
            output_dir=output_dir,
        ),
        storage=LocalArtifactStorageBackend(tmp_path / "artifacts"),
    )
    await recorder.prepare()
    await recorder.collect_after_tool_result(tool_name="execute_in_skill", tool_call_id="call-1")

    resp = await client.get(f"/api/conversations/{conv_id}/artifacts")
    assert resp.status_code == 200, resp.text
    artifacts = resp.json()
    assert len(artifacts) == 1
    artifact_id = artifacts[0]["id"]

    content_resp = await client.get(
        f"/api/conversations/{conv_id}/artifacts/{artifact_id}/content"
    )
    assert content_resp.status_code == 200
    assert "# Report" in content_resp.json()["text"]

    download_resp = await client.get(
        f"/api/conversations/{conv_id}/artifacts/{artifact_id}/download"
    )
    assert download_resp.status_code == 200
    assert download_resp.content == b"# Report"
```

```python
# backend/tests/test_artifact_library_router.py
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.services.artifact_service import ArtifactDeltaRecorder, ArtifactRuntimeContext
from app.services.artifact_storage import LocalArtifactStorageBackend
from tests.conftest import TEST_USER_ID, TestSession
from tests.test_artifact_service import _seed_conversation


@pytest.mark.asyncio
async def test_generated_file_library_search_favorite_and_stats(
    client: AsyncClient,
    tmp_path: Path,
) -> None:
    conv_id, agent_id = await _seed_conversation()
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / "report.md").write_text("# Report", encoding="utf-8")
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conv_id,
            user_id=TEST_USER_ID,
            agent_id=agent_id,
            assistant_msg_id="run-library-1",
            output_dir=output_dir,
        ),
        storage=LocalArtifactStorageBackend(tmp_path / "artifacts"),
    )
    await recorder.prepare()
    await recorder.collect_after_tool_result(tool_name="execute_in_skill", tool_call_id="call-1")

    list_resp = await client.get("/api/artifacts", params={"q": "report", "kind": "markdown"})
    assert list_resp.status_code == 200, list_resp.text
    page = list_resp.json()
    assert len(page["items"]) == 1
    artifact_id = page["items"][0]["id"]

    fav_resp = await client.patch(f"/api/artifacts/{artifact_id}", json={"is_favorite": True})
    assert fav_resp.status_code == 200
    assert fav_resp.json()["is_favorite"] is True

    stats_resp = await client.get("/api/artifacts/stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["total_count"] == 1
    assert stats["favorite_count"] == 1
    assert stats["by_kind"][0]["kind"] == "markdown"
```

- [ ] **Step 2: Run failing router tests**

```bash
cd backend
uv run pytest tests/test_artifacts_router.py tests/test_artifact_library_router.py -q
```

Expected: routes do not exist.

- [ ] **Step 3: Implement `artifacts.py` router**

Use the route contract in section 11.12. Register it in `backend/app/main.py`:

```python
from app.routers import artifacts
...
app.include_router(artifacts.router)
```

Follow the existing import block style around `uploads`, `shares`, and `conversations`.

- [ ] **Step 4: Fix legacy file endpoint owner guard**

In `get_conversation_file()`, replace `chat_service.get_conversation` with `chat_service.get_owned_conversation` as shown in section 11.11.

- [ ] **Step 5: Run router tests**

```bash
cd backend
uv run pytest tests/test_artifacts_router.py tests/test_artifact_library_router.py tests/test_conversations_router.py -q
```

Expected: pass.

### Task 6: Frontend types, store, and SSE handling

**Files:**

- Modify: `frontend/src/lib/types/index.ts`
- Create: `frontend/src/lib/stores/chat-artifacts.ts`
- Modify: `frontend/src/lib/chat/use-chat-runtime.ts`
- Test: `frontend/src/lib/stores/__tests__/chat-artifacts.test.ts`
- Test: `frontend/src/lib/chat/__tests__/use-chat-runtime-commit.test.tsx`

- [ ] **Step 1: Write artifact store test**

```ts
// frontend/src/lib/stores/__tests__/chat-artifacts.test.ts
import { createStore } from 'jotai'
import { describe, expect, it } from 'vitest'
import { chatArtifactsAtom, upsertChatArtifactAtom } from '../chat-artifacts'
import type { FileEventPayload } from '@/lib/types'

function payload(overrides: Partial<FileEventPayload> = {}): FileEventPayload {
  return {
    op: 'created',
    id: 'artifact-1',
    agent_id: 'agent-1',
    conversation_id: 'conv-1',
    assistant_msg_id: 'run-1',
    run_id: 'run-1',
    tool_call_id: 'call-1',
    source_tool_name: 'execute_in_skill',
    path: 'report.md',
    display_name: 'report.md',
    mime_type: 'text/markdown',
    extension: 'md',
    artifact_kind: 'markdown',
    size_bytes: 6,
    sha256: 'a'.repeat(64),
    status: 'ready',
    is_favorite: false,
    last_opened_at: null,
    preview_count: 0,
    download_count: 0,
    version_id: 'version-1',
    version_number: 1,
    created_at: '2026-06-05T00:00:00Z',
    updated_at: '2026-06-05T00:00:00Z',
    url: '/api/conversations/conv-1/artifacts/artifact-1',
    preview_url: '/api/conversations/conv-1/artifacts/artifact-1/content',
    download_url: '/api/conversations/conv-1/artifacts/artifact-1/download',
    ...overrides,
  }
}

describe('chat artifact store', () => {
  it('upserts by artifact id and removes deleted artifacts', () => {
    const store = createStore()
    store.set(upsertChatArtifactAtom, payload())
    store.set(upsertChatArtifactAtom, payload({ op: 'updated', version_number: 2 }))

    expect(store.get(chatArtifactsAtom)['conv-1']).toHaveLength(1)
    expect(store.get(chatArtifactsAtom)['conv-1'][0].version_number).toBe(2)

    store.set(upsertChatArtifactAtom, payload({ op: 'deleted' }))
    expect(store.get(chatArtifactsAtom)['conv-1']).toEqual([])
  })
})
```

- [ ] **Step 2: Run failing frontend store test**

```bash
cd frontend
pnpm test frontend/src/lib/stores/__tests__/chat-artifacts.test.ts
```

Expected: store file does not exist. If the project test script uses Vitest path syntax differently, use the same command pattern as existing frontend tests.

- [ ] **Step 3: Add TS types and store**

Use sections 11.13 and 11.14.

- [ ] **Step 4: Update `use-chat-runtime.ts`**

Import and set `upsertChatArtifactAtom`; add the `file_event` switch case. Existing tests mock `useSetAtom`, so ensure the new setter does not require provider changes.

- [ ] **Step 5: Run frontend tests**

```bash
cd frontend
pnpm test frontend/src/lib/stores/__tests__/chat-artifacts.test.ts frontend/src/lib/chat/__tests__/use-chat-runtime-commit.test.tsx
```

Expected: pass.

### Task 7: Artifact right rail and preview registry

**Files:**

- Modify: `frontend/src/lib/stores/chat-right-rail.ts`
- Modify: `frontend/src/components/chat/right-rail/chat-right-rail.tsx`
- Create: `frontend/src/components/chat/right-rail/artifact-panel-content.tsx`
- Create: preview provider files listed in section 11.3
- Modify: `frontend/messages/ko.json`
- Modify: `frontend/messages/en.json`
- Test: `frontend/src/components/chat/artifacts/__tests__/preview-registry.test.tsx`

- [ ] **Step 1: Write preview registry test**

```tsx
// frontend/src/components/chat/artifacts/__tests__/preview-registry.test.tsx
import { describe, expect, it } from 'vitest'
import { pickPreviewProvider } from '../preview-registry'
import { imagePreviewProvider } from '../providers/image-preview'
import { markdownPreviewProvider } from '../providers/markdown-preview'
import { fallbackPreviewProvider } from '../providers/fallback-preview'
import type { ArtifactSummary } from '@/lib/types'

function artifact(overrides: Partial<ArtifactSummary>): ArtifactSummary {
  return {
    id: 'a1',
    agent_id: 'agent-1',
    conversation_id: 'c1',
    assistant_msg_id: 'r1',
    run_id: 'r1',
    path: 'file.bin',
    display_name: 'file.bin',
    mime_type: 'application/octet-stream',
    extension: 'bin',
    artifact_kind: 'other',
    size_bytes: 1,
    sha256: 'a'.repeat(64),
    status: 'ready',
    is_favorite: false,
    last_opened_at: null,
    preview_count: 0,
    download_count: 0,
    version_id: 'v1',
    version_number: 1,
    created_at: '2026-06-05T00:00:00Z',
    updated_at: '2026-06-05T00:00:00Z',
    url: '#',
    preview_url: '#',
    download_url: '#',
    ...overrides,
  }
}

describe('artifact preview registry', () => {
  it('prefers specific providers over fallback', () => {
    const providers = [fallbackPreviewProvider, markdownPreviewProvider, imagePreviewProvider]
    expect(pickPreviewProvider(artifact({ mime_type: 'image/png', extension: 'png' }), providers).id).toBe('image')
    expect(pickPreviewProvider(artifact({ mime_type: 'text/markdown', extension: 'md' }), providers).id).toBe('markdown')
    expect(pickPreviewProvider(artifact({ mime_type: 'application/octet-stream' }), providers).id).toBe('fallback')
  })
})
```

- [ ] **Step 2: Run failing preview test**

```bash
cd frontend
pnpm test frontend/src/components/chat/artifacts/__tests__/preview-registry.test.tsx
```

Expected: provider files do not exist.

- [ ] **Step 3: Implement right rail state**

Apply section 11.15. Add i18n keys:

```json
// frontend/messages/ko.json
{
  "chat": {
    "rightRail": {
      "artifacts": "파일"
    },
    "artifacts": {
      "empty": "아직 생성된 파일이 없습니다.",
      "download": "다운로드",
      "previewUnavailable": "미리보기를 지원하지 않는 파일입니다."
    }
  }
}
```

```json
// frontend/messages/en.json
{
  "chat": {
    "rightRail": {
      "artifacts": "Files"
    },
    "artifacts": {
      "empty": "No files have been generated yet.",
      "download": "Download",
      "previewUnavailable": "Preview is not available for this file."
    }
  }
}
```

Merge these keys into existing JSON objects rather than replacing the whole files.

- [ ] **Step 4: Implement preview providers**

Use native elements for image/audio/video, `MarkdownContent` for Markdown, `MermaidDiagram` for Mermaid, and syntax highlighter/text fallback for code/text. Keep provider components small and lazy-load heavy pieces if bundle size rises.

- [ ] **Step 5: Run frontend checks**

```bash
cd frontend
pnpm test frontend/src/components/chat/artifacts/__tests__/preview-registry.test.tsx
pnpm lint:i18n
pnpm lint:design-system
```

Expected: pass.

### Task 8: Generated File Library UI

**Files:**

- Create: `frontend/src/app/artifacts/page.tsx`
- Create: `frontend/src/components/artifacts/artifact-library-content.tsx`
- Create: `frontend/src/components/artifacts/artifact-library-filters.tsx`
- Create: `frontend/src/components/artifacts/artifact-library-stats.tsx`
- Create: `frontend/src/lib/hooks/use-artifact-library.ts`
- Modify: `frontend/src/lib/api/artifacts.ts`
- Modify: `frontend/src/components/layout/app-sidebar.tsx`
- Modify: `frontend/src/components/layout/breadcrumb-nav.tsx`
- Modify: `frontend/messages/ko.json`
- Modify: `frontend/messages/en.json`
- Test: `frontend/src/lib/hooks/__tests__/use-artifact-library.test.tsx`

- [ ] **Step 1: Write library API hook test**

```tsx
// frontend/src/lib/hooks/__tests__/use-artifact-library.test.tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useArtifactLibrary } from '../use-artifact-library'

vi.mock('@/lib/api/artifacts', () => ({
  listArtifactLibrary: vi.fn(async () => ({
    items: [],
    next_cursor: null,
    has_more: false,
  })),
}))

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe('useArtifactLibrary', () => {
  afterEach(() => vi.clearAllMocks())

  it('returns generated file library results', async () => {
    const { result } = renderHook(
      () => useArtifactLibrary({ q: 'report', kind: 'markdown', favorite: true }),
      { wrapper },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.items).toEqual([])
  })
})
```

- [ ] **Step 2: Run failing hook test**

```bash
cd frontend
pnpm test frontend/src/lib/hooks/__tests__/use-artifact-library.test.tsx
```

Expected: hook/API files do not exist.

- [ ] **Step 3: Implement artifact library API client and hook**

`frontend/src/lib/api/artifacts.ts` should expose:

```ts
listConversationArtifacts(conversationId: string): Promise<ArtifactSummary[]>
listArtifactLibrary(params: ArtifactLibraryParams): Promise<ArtifactLibraryPage>
getArtifactLibraryStats(): Promise<ArtifactLibraryStats>
toggleArtifactFavorite(artifactId: string, isFavorite: boolean): Promise<ArtifactSummary>
recordArtifactOpened(artifactId: string): Promise<ArtifactSummary>
```

`useArtifactLibrary` should use a query key that includes `q`, `agent_id`, `conversation_id`, `kind`, `favorite`, and cursor.

- [ ] **Step 4: Implement `/artifacts` page**

The page should render the actual file library as the first screen:

- search input
- agent filter
- conversation filter
- kind filter
- favorite filter
- stats strip
- dense file list or grid
- preview/detail pane using the same `ArtifactPreview` and provider registry from Task 7

Do not make a landing page for this route.

- [ ] **Step 5: Add navigation and i18n**

Add a sidebar item pointing to `/artifacts` and breadcrumb label `nav.artifacts`. Add Korean and English copy under `artifacts.library`.

- [ ] **Step 6: Run library UI checks**

```bash
cd frontend
pnpm test frontend/src/lib/hooks/__tests__/use-artifact-library.test.tsx
pnpm lint:i18n
pnpm lint:design-system
```

Expected: pass.

### Task 9: End-to-end verification

**Files:**

- No required new files if backend/frontend unit coverage is enough.
- Add Playwright coverage only if an existing chat E2E suite is already active for this branch.

- [ ] **Step 1: Backend full targeted run**

```bash
cd backend
uv run pytest \
  tests/test_artifact_models.py \
  tests/test_artifact_paths.py \
  tests/test_artifact_storage.py \
  tests/test_artifact_service.py \
  tests/test_artifacts_router.py \
  tests/test_artifact_library_router.py \
  tests/integration/test_artifact_streaming.py \
  tests/integration/test_broker_dual_write.py \
  tests/integration/test_stream_resume.py \
  -q
```

Expected: pass.

- [ ] **Step 2: Frontend targeted run**

```bash
cd frontend
pnpm test \
  frontend/src/lib/stores/__tests__/chat-artifacts.test.ts \
  frontend/src/lib/hooks/__tests__/use-artifact-library.test.tsx \
  frontend/src/components/chat/artifacts/__tests__/preview-registry.test.tsx \
  frontend/src/lib/chat/__tests__/use-chat-runtime-commit.test.tsx
pnpm lint:i18n
pnpm lint:design-system
```

Expected: pass.

- [ ] **Step 3: Manual local verification**

Start servers with the project worktree port rules:

```bash
cd backend
uv run uvicorn app.main:app --reload --port 8001 --reload-dir app
```

```bash
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8001 pnpm dev -- --port 3000
```

Create or use a skill that writes `report.md` to `$SKILL_OUTPUT_DIR`, send a chat message that triggers `execute_in_skill`, and verify:

- SSE stream contains `event: file_event`.
- Right rail can open `Files`.
- `/artifacts` library shows the generated file.
- Library search finds `report.md`.
- Favorite toggle persists after refresh.
- Stats reflect total count and markdown kind count.
- `report.md` appears without waiting for final answer text links.
- Markdown preview renders in the side panel.
- Download endpoint returns the exact file bytes.
- Existing inline Markdown/Mermaid/image rendering in chat messages still works.
