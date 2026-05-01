# 삭제 분석 보고서 — 백로그 E M2 (MCP → Connection 이관)

**브랜치**: `feature/connection-mcp-migration`
**작성자**: 베조스 (QA)
**작성일**: 2026-04-18
**ADR**: `docs/design-docs/adr-008-connection-entity.md`
**실행계획**: `docs/exec-plans/active/backlog-e-connection-refactor.md` (M2 섹션)
**이전 보고서**: `tasks/deletion-analysis-e-m1.md`
**스코프 정의**: M2는 MCP 도구의 credential/서버 설정 해석 경로를 **connection 경유로 전환**하는 마일스톤. `tool.connection_id` 추가 + chat_service MCP 분기 재작성 + env_vars 템플릿 런타임 해석. `mcp_servers` / `tool.mcp_server_id` / `tool.auth_*` 컬럼 drop은 **M6 책임**. M2는 이관 + legacy fallback 공존 단계이므로 즉시 삭제는 거의 없어야 정상.

---

## 분석 원칙

ADR-008 §이행 전략 + `progress.txt` 파일 경계를 기준으로, M2 PR이 닫힐 때 안전하게 제거/단순화 가능한 항목만 분류한다. M2는 hot path(`chat_service.build_tools_config`)를 건드리는 **고위험 마일스톤**이므로 drive-by 삭제 절대 금지. legacy fallback (`connection_id IS NULL AND mcp_server_id IS NOT NULL`) 보장이 최우선.

---

## 사전 사실 확인

탐색 중 다음을 확인했다 (피차이 S2 시작 전 기준선):

- **`backend/app/models/tool.py:70-72, 90-92`** — `Tool.connection_id` 컬럼과 `connection` relationship이 **이미 모델에 선반영**되어 있다. 즉 S2(피차이)의 모델 변경분이 부분 머지된 상태. 피차이는 Alembic `m9` 작성 + `mcp_server_id` deprecate 주석 정리만 남음 (실제 컬럼 추가 마이그레이션 + 이관 SQL).
- **`tool.py:68`** — `mcp_server_id` 컬럼에 이미 `# deprecated: M6에서 제거 예정. 이관 기간 동안 legacy fallback 용도로 유지.` 주석 존재. 별도 표기 추가 불필요.

이 사실은 S2 담당자(피차이)에게 사티아가 전달해야 한다 — 작업 누락 방지.

---

## 즉시 삭제 가능

**0건.**

M2 종료 시점에도 `mcp_servers` 테이블, `Tool.mcp_server_id`, `MCPServer.auth_type/auth_config`, 프론트엔드 `mcp-server-auth-dialog`는 모두 그대로 살아있어야 한다 (legacy fallback / 후속 마일스톤 의존). 이번 PR에서 안전하게 지울 수 있는 코드는 존재하지 않는다.

---

## 단순화 제안 (M2 PR 안에서 처리)

### 1. **`backend/app/services/chat_service.py:174-187` — MCP/non-MCP 분기 통합** (젠슨 S3 가이드)

현재 구조:
```python
if tool.type == ToolType.MCP and tool.mcp_server:
    if tool.mcp_server.credential_id and tool.mcp_server.credential:
        cred_auth = resolve_credential_data(tool.mcp_server.credential)
    elif tool.mcp_server.auth_config:
        cred_auth = tool.mcp_server.auth_config
    else:
        cred_auth = {}
else:
    if tool.credential_id and tool.credential:
        cred_auth = resolve_credential_data(tool.credential)
    elif tool.auth_config:
        cred_auth = tool.auth_config
    else:
        cred_auth = {}
```

- 제안: 신규 우선순위 `connection → mcp_server(legacy fallback) → tool.credential → tool.auth_config → {}` 단일 함수로 추출. MCP 도구만 connection 우선, 그 외는 기존 그대로. 같은 4-way 폴백 트리가 두 분기에 중복되어 있는 게 본질적인 복잡도가 아니라 우연한 중복.
- 근거: M2 후 MCP 분기 안에 또 한 단계(connection)가 들어가면 4중 if 깊이가 됨. 헬퍼로 추출 → legacy fallback 제거(M6) 시 해당 함수 한 곳만 수정.
- 조치: 젠슨이 S3에서 `_resolve_tool_auth(tool)` 또는 `_resolve_mcp_auth(tool)` 헬퍼로 분리. **새 추상화 도입은 금지** — 기존 chat_service 모듈 안의 모듈-private 함수 1개만.

### 2. **`backend/app/services/chat_service.py:200-202` — `mcp_server_url` 출력 경로** (젠슨 S3 가이드)

현재:
```python
if tool.type == ToolType.MCP and tool.mcp_server:
    config_entry["mcp_server_url"] = tool.mcp_server.url
    config_entry["mcp_tool_name"] = tool.name
```

- 제안: M2 후 `tool.connection` 경유 시 `connection.extra_config["url"]`에서 읽고, fallback 시에만 `tool.mcp_server.url`. 키 이름은 그대로 `mcp_server_url` 유지 (다운스트림 호환).
- 근거: ADR-008 §2 — MCP url은 `extra_config.url`이 SOT. 이관 후 `mcp_server.url`은 legacy.
- 조치: 젠슨 S3에서 우선순위 분기.

### 3. **`backend/app/services/credential_service.py:110-119` — `resolve_server_auth` 시그니처 확장** (젠슨 S3 가이드, 단 credential_service 파일 자체는 베조스/젠슨 모두 수정 금지 영역)

- 현재: `resolve_server_auth(server: MCPServer) -> dict | None` — MCPServer만 받음.
- M2 후 필요: connection 경유 해석. MCPServer fallback 그대로 유지.
- 제안: **이 함수는 변경 금지**. 대신 chat_service나 mcp_client 측에 `_resolve_connection_auth(connection: Connection)` 신규 헬퍼를 분리해 두 함수를 호출자가 적절히 선택하게. 이유는 (a) credential_service.py는 progress.txt 파일 경계상 M2 범위 밖, (b) 시그니처 변경 시 백로그 C에서 안정화한 회귀 테스트 흔들림.
- 근거: 베조스 M1 보고서 #5와 동일한 결론 — `resolve_server_auth`는 M6 cleanup 항목.
- 조치: 젠슨이 S3에서 `_resolve_connection_auth`를 새 위치(`agent_runtime/mcp_client.py` 또는 `chat_service.py` 모듈-private)에 만들고 import. credential_service.py 시그니처 손대지 말 것.

### 4. **`backend/app/agent_runtime/mcp_client.py:12-19` — `auth_config` 입력 형식 확장** (젠슨 S3 가이드)

현재 `test_mcp_connection(url, auth_config)`은 `auth_config = {api_key, header_name}` 형식 전제. ADR-008 §2의 `extra_config = {url, auth_type, headers?, env_vars?, transport?, timeout?}`와 시맨틱 차이.

- 제안: 시그니처는 **그대로 유지**(외부 호출자 회귀 0). 함수 내부에서 `auth_config`가 새 `extra_config` 모양인지 legacy 모양인지 판별하는 분기 추가는 금지. 대신 호출 측(tools_router, 신규 connection 등록 경로)에서 사전 변환 → mcp_client는 dumb httpx 호출자 역할 유지.
- 근거: dual-shape 입력 함수는 테스트 케이스가 곱연산으로 늘어난다(보안/회귀 양쪽). single-shape + 변환 책임 호출자 위임이 단순.
- 조치: 젠슨 S3에서 connection→legacy auth_config 변환 1줄을 호출 직전에 인라인 처리. 별도 어댑터 클래스 만들지 말 것.

### 5. **테스트 격리 정책 — 신규 파일 1개 추가, 기존 회귀는 동작 보존만** (베조스 본인 가이드, S4)

- 제안: `tests/test_connection_mcp_resolve.py` 신규 1개에 5 시나리오 모두 격납. `tests/test_mcp_connection.py`와 `tests/test_tools_router_extended.py`는 **기대값(시맨틱) 변경 없는 갱신만** — connection 컬럼 신설로 인한 fixture 추가/마이그레이션 헤드 변경 등 mechanical change에 한정.
- 근거: chat_service hot path 변경 PR에서 기존 테스트 시맨틱이 바뀌면 그 자체가 regression 시그널이다 (M1 보고서 #4 동일 원칙). Legacy fallback 경로가 끊기지 않았는지 검증하려면 기존 회귀 시맨틱 보존이 필수.
- 조치: 베조스 S4에서 `test_mcp_connection.py` diff는 가능한 0줄을 목표로. 변경 필요 시 사티아에게 사유 보고.

---

## 보류 (후속 마일스톤으로 이월)

이전 M1 보고서의 8건 + M2 분석에서 추가 식별. **M2 PR에서 절대 건드리지 않음.**

| # | 위치 | 현재 역할 | 권고 시점 | 사유 |
|---|------|-----------|-----------|------|
| 1 | `backend/app/models/tool.py:35-58` `MCPServer` 테이블 전체 | MCP 서버 메타데이터 | **M6** (`m12_drop_legacy_columns`) | M3-M5 기간 legacy fallback 진실 기준. 사용자 데이터 이관 미완료 row 안전망. |
| 2 | `backend/app/models/tool.py:69` `Tool.mcp_server_id` FK | MCP tool → MCPServer 링크 | **M6** | M2 m9가 `connection_id`만 채우고 `mcp_server_id`는 그대로. M3-M5 동안 둘 공존. |
| 3 | `backend/app/models/tool.py:78-82` `Tool.auth_type`, `Tool.auth_config`, `Tool.credential_id` | PREBUILT/CUSTOM 도구 inline auth | **M4-M6** | M4(`m11_migrate_custom_credentials`)에서 connection으로 이관, M6 drop. M2 스코프 외. |
| 4 | `backend/app/models/tool.py:30` `AgentToolLink.config` JSON | per-agent 도구 override | **M6** | ADR-008 §3 — `agent_tools.connection_id`(M3+)로 대체, M6 drop. |
| 5 | `backend/app/services/credential_service.py:110-119` `resolve_server_auth` | MCPServer auth 해석 헬퍼 | **M6** | M2-M5 기간 legacy fallback 경로에서 호출됨. 단순화 #3 참조. |
| 6 | `backend/app/services/credential_service.py:135-140` `get_usage_count`의 `mcp_count` | credential 사용량 집계 | **M5/M6** | M5에서 `connection_count` 추가 후 M6 drop. M2에서는 의미 유지. |
| 7 | `frontend/src/components/tool/mcp-server-auth-dialog.tsx` | MCP 서버 인증 편집 UI | **M5** (백로그 F) | `ConnectionBindingDialog` 통합 흡수. M2 스코프 외. |
| 8 | `frontend/src/components/tool/add-tool-dialog.tsx` MCP 탭 분기 | MCP 도구 추가 흐름 | **M5** (백로그 F) | connection 선택 UX로 재작성. M2 스코프 외. |
| 9 | `backend/app/agent_runtime/mcp_client.py:80-98` `list_mcp_tools` | MCP tool discovery | **M6** | url 인자만 받음. M2에서 변경 불필요. |
| 10 | `backend/tests/test_mcp_client.py` 전체 | mcp_client 단위 테스트 | **M5/M6** | 단순화 #4에 따라 mcp_client 시그니처 보존 → 회귀 테스트 그대로. |

---

## 보안/회귀 점검 (M2 PR에서 반드시 검증)

탐색 중 발견한 잠재 리스크. 베조스 S4 신규 테스트에 반영:

1. **env_vars 템플릿 평문 노출 위험**
   - `${credential.<field_name>}`이 미해석 상태로 외부(LLM 입력, 로그, API 응답)에 새어나가면 안 됨. 반대로 해석 후 평문이 connection.extra_config로 다시 영속화되면 ADR §보안 위반.
   - 검증: `test_connection_mcp_resolve.py`에 (a) ConnectionResponse에 env_vars 평문 미포함, (b) 런타임 해석 후 DB 재기록 없음 시나리오.
2. **legacy fallback 시 credential 노출 일관성**
   - `connection_id IS NULL` 분기에서도 `MCPServer.auth_config` 평문이 응답으로 흘러나가지 않아야 함 (기존 동작과 동일).
   - 검증: 기존 `test_mcp_connection.py` 회귀가 잡고 있는지 확인. 비어 있으면 신규 1 케이스 추가.
3. **마이그레이션 데이터 무결성**
   - `mcp_servers` row 수 = M2 후 `connections WHERE type='mcp'` row 수. tool.mcp_server_id 비어있지 않은 row 수 = M2 후 tool.connection_id 채워진 MCP tool row 수.
   - 검증: 신규 `test_connection_mcp_resolve.py`에 row count assertion (피차이 m9 마이그레이션 검증).

---

## 결론

| 분류 | 건수 | 비고 |
|------|------|------|
| **즉시 삭제** | **0건** | M2는 이관 + legacy fallback 공존 단계. drop은 M6. |
| **단순화 제안** | **5건** | 모두 S3(젠슨)/S4(베조스) 신규 작업 가이드. 기존 모듈 시그니처 변경 0. |
| **보류 (후속 마일스톤)** | **10건** | M4 1건, M5 4건, M5/M6 1건, M6 4건. M1 보고서 8건과 정합 + MCP-구체 2건 추가(`mcp_client.list_mcp_tools`, `test_mcp_client.py`). |

**베조스 판단**: M2는 hot path(`chat_service.build_tools_config`) 재작성 + 신규 데이터 마이그레이션 + 신규 런타임 템플릿 해석이 동시에 들어오는 **고위험 마일스톤**이다. 코드 삭제·시그니처 변경을 끼워넣으면 회귀 영역이 곱연산으로 폭발한다. legacy fallback은 M2의 **안전망**이며 M6까지 보존이 ADR §이행 전략의 핵심 약속.

단순화 5건은 신규 헬퍼 분리 + 호출자 책임 유지 원칙으로, 이미 hot path를 건드리는 PR 안에서 추가 표면적을 만들지 않는 방향이다. 단순화 #3은 특히 `credential_service.py`를 **건드리지 말 것**을 명시 — progress.txt 파일 경계 위반 방지.

**사티아 보고**: 사전 확인 사실 (Tool.connection_id 모델 선반영) + 단순화 #3-#4(파일 경계 충돌 회피)는 S2/S3 담당자에게 명시적으로 전달 권고.
