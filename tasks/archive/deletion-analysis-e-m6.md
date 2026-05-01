# M6 삭제 분석 (베조스) — 2026-04-21

**담당**: 베조스 (QA / Musk Step 2)
**base**: main @ `ad8c0fd` (PR #58 M5 머지)
**worktree**: `/Users/chester/dev/natural-mold/.claude/worktrees/backlog-e-m6`
**참고**: `CHECKPOINT.md` (스코프 locked), `progress.txt` (gotchas), `HANDOFF.md`
**판정**: **주의 (CAUTION)** — agent_tools.config drop은 **세 부분을 동시에** 정리해야 회귀 없음

범례: `[D]`=삭제 / `[S]`=단순화(부분 편집) / `[K]`=유지

---

## 제거 타깃 확정 (파일:라인 단위)

### 1. 모델 레이어

- `[D] backend/app/models/tool.py:35-58` — `MCPServer` 클래스 전체
- `[D] backend/app/models/tool.py:72` — `Tool.mcp_server_id` FK
- `[D] backend/app/models/tool.py:82` — `Tool.auth_config` 컬럼
- `[D] backend/app/models/tool.py:83-85` — `Tool.credential_id` FK
- `[D] backend/app/models/tool.py:92` — `Tool.mcp_server` relationship
- `[D] backend/app/models/tool.py:96-98` — `Tool.credential` relationship
- `[D] backend/app/models/tool.py:17-32` — `AgentToolLink.config` 필드 (line 30) — **agent_tools.config drop 확정**
- `[S] backend/app/models/__init__.py:12, 22` — `MCPServer` export 제거
- `[K] backend/app/models/tool.py:61-98` — `Tool` 클래스 자체와 `connection_id`/`connection` relationship은 유지

### 2. 서비스 레이어

#### 2-1. chat_service.py

- `[D] backend/app/services/chat_service.py:18` — import `MCPServer` 제거
- `[D] backend/app/services/chat_service.py:26-27` — `resolve_server_auth` import 제거
- `[D] backend/app/services/chat_service.py:195-199` — `selectinload(Tool.credential)` + `Tool.mcp_server ... MCPServer.credential` 블록 제거 (legacy eager-load)
- `[S] backend/app/services/chat_service.py:195-199` — `selectinload(Tool.credential)` 만은 **유지 혹은 같이 삭제**? → M6 이후 `tool.credential_id` 컬럼이 없으므로 반드시 **같이 삭제**. 유지 시 AttributeError.
- `[D] backend/app/services/chat_service.py:313-340` — `_resolve_custom_auth` 내 bridge override 전체 삭제. connection_id 없을 시 error raise로 전환 (legacy 경로 제거).
- `[S] backend/app/services/chat_service.py:301-340` — `_resolve_custom_auth`를 M6 후 단순화: `connection_id IS NULL` → `ToolConfigError`, 있으면 Gate A → Gate B 직결.
- `[D] backend/app/services/chat_service.py:343-356` — `_resolve_legacy_tool_auth` 함수 전체 삭제. PREBUILT의 `provider_name IS NULL` 케이스, BUILTIN의 legacy 케이스 같이 제거.
- `[S] backend/app/services/chat_service.py:427-443` — PREBUILT/CUSTOM 분기 단순화: `provider_name IS NULL` 분기 제거(PREBUILT는 provider_name 강제), CUSTOM은 connection 경로만.
- `[D] backend/app/services/chat_service.py:417-424` — MCP `elif tool.mcp_server is not None` legacy fallback 블록 제거. `tool.connection_id IS NOT NULL` 필수화.
- `[S] backend/app/services/chat_service.py:376-426` — MCP 분기: connection 경로만 남기고 else에서 `ToolConfigError` raise. `cred_auth = {}` 복원 경로 제거.
- `[D] backend/app/services/chat_service.py:441-443` — `else` (BUILTIN fallback 경로의 `_resolve_legacy_tool_auth` 호출) 제거. BUILTIN은 auth 없이 동작이므로 `cred_auth = {}` 로 변경.
- `[D] backend/app/services/chat_service.py:445` — **★ `merged_auth = {**cred_auth, **(link.config or {})}` → `merged_auth = cred_auth` 로 단순화 ★** (agent_tools.config drop 확정 시)
- `[S] backend/app/services/chat_service.py:40-49` — `__all__`에서 `_resolve_legacy_tool_auth` 제거

#### 2-2. credential_service.py

- `[D] backend/app/services/credential_service.py:11` — import `MCPServer` 제거
- `[D] backend/app/services/credential_service.py:110-119` — `resolve_server_auth()` 함수 전체 삭제
- `[D] backend/app/services/credential_service.py:135-140` — `get_usage_count`의 `mcp_count_result` 블록 삭제
- `[S] backend/app/services/credential_service.py:122-142` — `get_usage_count` 반환에서 `mcp_server_count` 키 제거 또는 0 고정. **caller 확인 필요** (`routers/credentials.py` grep).

#### 2-3. tool_service.py

- `[D] backend/app/services/tool_service.py:10` — import `MCPServer` 제거
- `[D] backend/app/services/tool_service.py:11` — `MCPServerCreate` import 제거
- `[D] backend/app/services/tool_service.py:89-130` — `register_mcp_server()` 함수 전체 삭제
- `[D] backend/app/services/tool_service.py:133-140` — `get_mcp_servers()` 삭제
- `[D] backend/app/services/tool_service.py:143-189` — `list_mcp_server_items()` 삭제
- `[D] backend/app/services/tool_service.py:207-238` — `update_mcp_server()` 삭제
- `[D] backend/app/services/tool_service.py:241-260` — `delete_mcp_server()` 삭제
- `[S] backend/app/services/tool_service.py:192-204` — `_apply_credential_update` 시그니처에서 `Tool | MCPServer` → `Tool`만. **함수 자체가 M6 후 `tool.credential_id`도 없으므로 제거** 가능 검토.
- `[S] backend/app/services/tool_service.py:263-294` — `update_tool_auth_config`에서 `auth_config` / `credential_id` 필드 처리 제거. → M6 이후 이 엔드포인트 자체가 의미 없음 → **라우터 단에서 제거 결정 권고 (피차이/젠슨 확정)**.

#### 2-4. agent_service.py — **agent_tools.config drop 연쇄**

- `[D] backend/app/services/agent_service.py:45-50` — `_build_tool_links`에서 `config_map` 파라미터 제거, `AgentToolLink(tool_id=tid, config=...)` → `AgentToolLink(tool_id=tid)`
- `[D] backend/app/services/agent_service.py:74-77` — `config_map` 구성 블록 삭제
- `[S] backend/app/services/agent_service.py:97-98` — `_build_tool_links(tool_ids_to_link, config_map)` → `_build_tool_links(tool_ids_to_link)`
- `[D] backend/app/services/agent_service.py:124-137` — `tool_configs` 처리 `if/elif` 블록 전체 삭제. `if data.tool_ids is not None:` 만 남기고 단순화.

### 3. 라우터/스키마

#### 3-1. routers/tools.py

- `[D] backend/app/routers/tools.py:49-56` — `POST /api/tools/mcp-server`
- `[D] backend/app/routers/tools.py:58-63` — `GET /api/tools/mcp-servers`
- `[D] backend/app/routers/tools.py:66-77` — `PATCH /api/tools/mcp-servers/{server_id}`
- `[D] backend/app/routers/tools.py:80-88` — `DELETE /api/tools/mcp-servers/{server_id}`
- `[D] backend/app/routers/tools.py:91-112` — `POST /api/tools/mcp-server/{server_id}/test` (`resolve_server_auth` 호출 포함)
- `[S] backend/app/routers/tools.py:10-18` — imports에서 `MCPServerCreate, MCPServerListItem, MCPServerResponse, MCPServerUpdate` 제거
- `[S] backend/app/routers/tools.py:9` — `mcp_server_not_found` import 제거
- `[D/S] backend/app/routers/tools.py:115-128` — `PATCH /api/tools/{tool_id}/auth-config` — **호출처 없으면 삭제**. 프론트에서 `updateAuthConfig`는 M6 dead API로 확인됨(아래 §4). 라우터도 함께 삭제 권장 — 젠슨 최종 확정.

#### 3-2. routers/agents.py — agent_tools.config drop 연쇄

- `[S] backend/app/routers/agents.py:40` — `ToolBrief(id=..., name=..., agent_config=link.config)` → `ToolBrief(id=..., name=...)`
- `[S] backend/app/schemas/agent.py:12-16` — `ToolConfigEntry` class 전체 삭제
- `[S] backend/app/schemas/agent.py:41` — `AgentCreate.tool_configs` 필드 제거
- `[S] backend/app/schemas/agent.py:54` — `AgentUpdate.tool_configs` 필드 제거
- `[S] backend/app/schemas/agent.py:68-73` — `ToolBrief.agent_config` 필드 제거

#### 3-3. schemas/tool.py

- `[D] backend/app/schemas/tool.py:57-62` — `MCPServerCreate`
- `[D] backend/app/schemas/tool.py:102-112` — `MCPServerResponse`
- `[D] backend/app/schemas/tool.py:123-134` — `MCPServerListItem`
- `[D] backend/app/schemas/tool.py:137-142` — `MCPServerUpdate`
- `[D] backend/app/schemas/tool.py:115-120` — `CredentialBrief` — `MCPServerListItem`에서만 사용. 다른 사용처 있으면 유지. **확인 필요**.
- `[D] backend/app/schemas/tool.py:50-54` — `ToolAuthConfigUpdate` (PATCH auth-config 라우터 삭제 시)
- `[D] backend/app/schemas/tool.py:11-26` — `AUTH_CONFIG_MASK` 상수 + `_reject_mask_sentinel`
- `[D] backend/app/schemas/tool.py:70` — `ToolResponse.mcp_server_id`
- `[D] backend/app/schemas/tool.py:71` — `ToolResponse.credential_id`
- `[D] backend/app/schemas/tool.py:79` — `ToolResponse.auth_config`
- `[D] backend/app/schemas/tool.py:86-99` — `ToolResponse._mask_auth_config` field_serializer
- `[D] backend/app/schemas/tool.py:45` — `ToolCustomCreate.auth_config` (옵션)
- `[S] backend/app/schemas/tool.py:46` — `ToolCustomCreate.credential_id` 유지? → M6 후 tool.credential_id 컬럼도 drop. POST /api/tools/custom이 credential_id를 받고 `connection_id`에만 쓰도록 정리해야 함. **여기 scope creep 주의** — 이 값은 connection_id derivation이라 M4에서 이미 connection으로 이관됨(tool_service.py:57-66). `credential_id` 필드는 receive 후 무시되어 이관 친화적으로 작성됨. M6에서 완전 제거 안전.
- `[D] backend/app/schemas/tool.py:46` — `ToolCustomCreate.credential_id` 도 scope에 포함 권장 (선택).

### 4. 프론트엔드

#### 4-1. lib/api/tools.ts

- `[D] frontend/src/lib/api/tools.ts:15-24` — `registerMCPServer`, `testMCPConnection` 메서드 삭제
- `[D] frontend/src/lib/api/tools.ts:25-36` — `updateAuthConfig` 메서드 삭제 (PATCH 라우터 삭제 시)
- `[D] frontend/src/lib/api/tools.ts:38-45` — `listMCPServers`, `updateMCPServer`, `deleteMCPServer` 삭제
- `[S] frontend/src/lib/api/tools.ts:1-9` — imports 축소

#### 4-2. lib/hooks/use-tools.ts

- `[D] frontend/src/lib/hooks/use-tools.ts:39-43` — `useRegisterMCPServer`
- `[D] frontend/src/lib/hooks/use-tools.ts:52-60` — `useUpdateToolAuthConfig` (라우터 삭제 시)
- `[D] frontend/src/lib/hooks/use-tools.ts:71-80` — `useMCPServers`
- `[D] frontend/src/lib/hooks/use-tools.ts:82-105` — `useToolsByConnection`의 MCP 경로 — `t.mcp_server_id` 의존 (line 99)
- `[D] frontend/src/lib/hooks/use-tools.ts:113-128` — `useUpdateMCPServer`, `useDeleteMCPServer`

#### 4-3. lib/types/index.ts

- `[D] frontend/src/lib/types/index.ts:220` — `Tool.mcp_server_id`
- `[D] frontend/src/lib/types/index.ts:229` — `Tool.auth_config`
- `[D] frontend/src/lib/types/index.ts:231` — `Tool.credential_id`
- `[D] frontend/src/lib/types/index.ts:318-326` — `MCPServer` 타입
- `[D] frontend/src/lib/types/index.ts:334-344` — `MCPServerListItem`
- `[D] frontend/src/lib/types/index.ts:346-350` — `MCPServerUpdateRequest`
- `[D] frontend/src/lib/types/index.ts:364-370` — `MCPServerCreateRequest`
- `[S] frontend/src/lib/types/index.ts:352-362` — `ToolCustomCreateRequest.auth_config` 제거 (선택)

#### 4-4. 참조처 cascade (타입 drop으로 빌드 에러 나는 곳만 **삭제**, 신규 로직 금지)

- `[S] frontend/src/app/tools/page.tsx:20, 40, 98-111, 401, 477-500, 529, 634-644` — MCP section / `mcp_server_id` 사용처 / `auth_config` configured-state 추론 / `useMCPServers` 모두 제거. **이 파일은 저커버그 S4 범위 초과 위험 — 별도 cleanup 필요**.
- `[S] frontend/src/components/tool/add-tool-dialog.tsx:21, 47` — `useRegisterMCPServer` 호출 제거. MCP 등록 UI 자체가 M5 이후 dead이므로 삭제 권장.
- `[D] frontend/src/components/tool/mcp-server-rename-dialog.tsx` — 파일 전체 삭제
- `[D] frontend/src/components/tool/mcp-server-group-card.tsx` — 파일 전체 삭제
- `[S] frontend/src/components/connection/connection-binding-dialog.tsx:35, 80, 467-506-521` — `useUpdateMCPServer` 사용 제거. MCP binding은 credential_id 만 업데이트하는 것이 아니라 connection 자체를 통해 가야 함. **M5.5/M6.1 옵션 D 영역 — M6 범위에서 단순 제거 불가. 저커버그 S4와 피차이/사티아 합의 필요.**

> **⚠ 저커버그 S4 scope creep 경고**: `app/tools/page.tsx`, `add-tool-dialog.tsx`, `connection-binding-dialog.tsx`, `mcp-server-*-dialog` 은 CHECKPOINT.md S4 "thin cleanup" 범위를 초과할 수 있다. 사티아에게 M6.1 로 분리하는 승인 요청 권고.

### 5. 테스트 (유지/삭제 결정)

#### 5-1. 전체 삭제 (legacy 전용)

- `[D] backend/tests/test_agent_tool_config.py` — **파일 전체 (142 lines, 4 tests)**. `tool_configs` / `agent_config` 필드가 drop되면 모든 테스트 broken. **단, agent_tools.config drop 확정 선결.**
- `[D] backend/tests/test_tools.py:509-577` — `test_build_tools_config_mcp_uses_server_credential` (legacy server_credential 경로).
- `[D] backend/tests/test_tools.py:267-404` — `_seed_mcp_server_with_tools` 및 `test_list_mcp_servers_returns_tool_count` / `test_update_mcp_server_*` / `test_delete_mcp_server_cascades_tools` (MCP server CRUD 섹션 전체)
- `[D] backend/tests/test_tools.py:60-74` — `test_register_mcp_server`
- `[D] backend/tests/test_tools.py:93-146` — `test_patch_tool_auth_config_preserves_unset_fields` (auth-config PATCH 라우터 삭제 시)
- `[D] backend/tests/test_tools.py:148-175` — `test_patch_mcp_tool_rejects_other_user`
- `[D] backend/tests/test_tools.py:177-221` — `test_tool_response_masks_auth_config_string_values`
- `[K] backend/tests/test_tools.py:24-59` — `test_create_custom_tool` 등 CUSTOM 관련은 유지
- `[D] backend/tests/test_tools_router_extended.py:62-85` — `test_test_mcp_connection_success`
- `[D] backend/tests/test_tools_router_extended.py:86-99` — `test_test_mcp_connection_server_not_found`
- `[D] backend/tests/test_tools_router_extended.py:100-150` — `test_update_auth_config_*` 3종 (라우터 삭제 시)
- `[D] backend/tests/test_tools_router_extended.py:152-175` — `test_mcp_server_register_via_api`
- `[K] backend/tests/test_tools_router_extended.py:177-430` — provider_name / connection 관련 유지
- `[D] backend/tests/test_conversations_router.py:40-58, 270-282` — `AgentToolLink(... config={"extra": "cfg"})` 및 merged auth assertion 제거. **테스트 자체는 유지하되 fixture 단순화**: `auth_config`/`link.config` 없이 PREBUILT connection 경로로 재작성 or agent_tools.config 시나리오 전체 삭제 중 선택. **추천: fixture 단순화**.

#### 5-2. 부분 삭제 (connection path 유지)

**`test_connection_mcp_resolve.py` (850 lines)** — 함수별 유지/삭제 표:

| line | 함수 | 결정 | 근거 |
|---|---|---|---|
| 216 | `test_build_tools_config_uses_connection_extra_config` | `[K]` | connection path |
| 382 | `test_build_tools_config_legacy_mcp_server_fallback` | `[D]` | legacy fallback 경로 |
| 427 | `test_build_tools_config_legacy_fallback_inline_auth_config` | `[D]` | legacy `auth_config` |
| 468 | `test_connection_takes_precedence_over_mcp_server` | `[D]` | precedence 테스트이나 legacy 쪽이 제거되면 의미 없음 |
| 522 | `test_template_regex_contract` | `[K]` | env_vars 템플릿 |
| 656 | `test_resolve_env_vars_rejects_non_dict_shape` | `[K]` | |
| 671 | `test_tool_config_error_is_app_error` | `[K]` | |
| 690 | `test_build_tools_config_forwards_connection_headers` | `[K]` | connection path |
| 769 | `test_migrated_extra_config_passes_strict_schema` | `[K]` | m9 contract |
| 817 | `test_response_tolerates_legacy_non_string_env_var_values` | `[K]` | |
| 857 | `test_response_redacts_env_var_secret_values` | `[K]` | |
| 889 | `test_extra_config_rejects_migration_sentinel_leak` | `[K]` | |
| 910 | `test_m9_generates_env_vars_from_credential_field_keys` | `[K]` | m9 contract — downgrade 불가 명시해도 m9 contract 자체는 유지 |
| 938-1029 | `test_mcp_credential_*` 4종 | `[K]` | connection path |
| 1125 | `test_response_validator_does_not_mutate_input_dict` | `[K]` | |
| 1169 | `test_response_redacts_header_values` | `[K]` | |
| 1217 | `test_distinct_transport_headers_create_separate_mcp_groups` | `[K]` | |
| 1281 | `test_executor_server_key_is_deterministic_across_calls` | `[K]` | |
| 1318 | `test_m9_skips_unrecoverable_credential_backed_server` | `[S]` | m9 helper 테스트는 유지하되 DB 생성 fixture에서 `MCPServer` 참조 → 파일 로드 실패 위험. **fixture 재작성 필요** — m9 모듈 helper만 직접 호출하는 방식으로 격리. |

**`test_connection_custom_resolve.py` (850 lines)** — 함수별:

| line | 함수 | 결정 | 근거 |
|---|---|---|---|
| 224 | `test_custom_resolves_current_user_connection_not_other_user` | `[K]` | |
| 288 | `test_custom_with_active_connection_resolves_credential` | `[K]` | |
| 327 | `test_custom_disabled_connection_fails_closed` | `[K]` | |
| 360 | `test_custom_connection_with_null_credential_fails_closed` | `[K]` | |
| 399 | `test_custom_bridge_override_when_tool_credential_rotated` | `[D]` | **bridge override 제거** |
| 448 | `test_custom_bridge_override_blocked_by_disabled_connection` | `[D]` | bridge override |
| 499 | `test_custom_resolves_raises_when_connection_missing_despite_fk` | `[S]` | connection_id NULL 경로 → ToolConfigError 기대값 변경 |
| 528 | `test_custom_legacy_credential_path_preserved` | `[D]` | legacy path |
| 565 | `test_custom_legacy_inline_auth_config_returned_as_is` | `[D]` | legacy path |
| 601 | `test_custom_rejects_connection_credential_user_mismatch` | `[K]` | |
| 650 | `test_m11_revision_ids_and_marker_are_stable` | `[K]` | m11 contract |
| 661 | `test_m11_migrate_custom_credentials_source_contract` | `[K]` | m11 contract |
| 719 | `test_m11_preserves_tool_credential_id_for_legacy_fallback` | `[S]` | m11 당시 `tool.credential_id` 존재 전제 — m12 이후에도 m11 helper 자체는 돌아가지만 통합 검증 맥락 상 **변경 필요**. 젠슨에게 위임. |
| 747 | `test_m11_downgrade_only_deletes_seed_marker_rows` | `[K]` | |
| 794 | `test_m11_upgrade_dedup_preexisting_custom_duplicates` | `[K]` | |

**`test_connection_prebuilt_resolve.py`** — provider_name NULL fallback 시나리오 (line 552: `auth_config={"api_key": "legacy-inline-key"}`)만 제거. 나머지 `[K]`.

**`test_executor.py`** — `tools_config` 입력 기반 (fixture). MCP legacy fallback 포함 케이스 재확인 필요. 젠슨이 S3 구현 중 `build_tools_config` 출력 시그니처 확정 후 2차 정리.

#### 5-3. agent_tools.config 관련 테스트

- `[D] backend/tests/test_agent_service_extended.py:89-110` — `test_create_agent_with_tool_configs`
- `[D] backend/tests/test_agent_service_extended.py:180-210` — `test_update_agent_tool_configs_only`
- `[S] backend/tests/test_assistant_read_tools.py:53, 326` — `AgentToolLink(... config=None)` → `AgentToolLink(... )` (model change 따라 자동 fail)
- `[S] backend/tests/test_assistant_write_tools.py:53` — 동일
- `[S] backend/tests/test_chat_service.py:227` — 동일
- `[S] backend/tests/test_connection_mcp_resolve.py:119, 616, 740` — 동일
- `[S] backend/tests/test_connection_custom_resolve.py:213, test_connection_prebuilt_resolve.py:198` — 동일
- `[S] backend/tests/test_trigger_executor.py:55` — `config={"agent_override": "ov"}` → 제거. agent_override 검증 블록도 함께 제거.
- `[S] backend/tests/test_tools.py:567` — OK (이미 `config` 없이 추가)

#### 5-4. Assistant 내부 도구 (★ agent_tools.config drop 시 수정 필수)

- `[S] backend/app/agent_runtime/assistant/tools/write_tools.py:312-330` — `update_tool_config` 도구 정의 전체 삭제 또는 no-op. **필수**. 그대로 두면 `link.config` AttributeError.
- `[S] backend/app/agent_runtime/assistant/tools/read_tools.py:60-103` — `get_agent_config` 반환 dict에서 `tools_info`의 `config` 키 제거.
- `[S] backend/app/agent_runtime/assistant/tools/read_tools.py:124-146` — `get_tool_config` 도구 정의 자체 제거 or 단순화 (config 반환 제거 시 거의 무의미 → **삭제 권장**).
- `[S] backend/app/schemas/assistant.py:57-62` — `AgentToolInfo.agent_config` 필드 제거 (존재 시).

> **이 §5-4 를 빠뜨리면 AI 에이전트 생성 대화 중 도구가 터짐. 젠슨 S3에서 필수 포함.**

---

## agent_tools.config 안전성 판단

### 조사 결과

**Write 경로**:
1. `POST /api/agents` (routers/agents.py → agent_service.create_agent:60-106) — API body의 `tool_configs: [{tool_id, config}]`를 `AgentToolLink.config`에 저장.
2. `PUT /api/agents/{id}` (agent_service.update_agent:109-144) — `tool_configs` 입력 시 config 업데이트.
3. **Assistant 내부 `update_tool_config` 도구** (write_tools.py:327) — AI 에이전트 생성 중 대화형으로 link.config를 덮어씀.

**Read 경로**:
1. `GET /api/agents/{id}` → `_agent_to_response` (routers/agents.py:40) → `ToolBrief.agent_config`
2. `chat_service.build_tools_config:445` → `merged_auth = {**cred_auth, **(link.config or {})}` — **실제 런타임 auth에 merge됨**
3. `read_tools.py:70, 142` — assistant `get_agent_config` / `get_tool_config` JSON 응답

**프론트엔드 사용 여부**:
- `agent_config` / `tool_configs` / `toolConfigs` / `agentConfig` grep: **No matches found** in `frontend/src/`
- **프론트엔드 UI는 이 필드를 절대 전송/표시하지 않음**

**현재 DB에 값이 저장되어 있을 가능성**:
- **LOW to MEDIUM**.
- PoC 환경(mock user) + UI write 경로 없음 → 대부분의 실제 세팅에서 NULL.
- 그러나 (a) 과거 TASKS의 "per-agent tool config (예: Google Chat webhook_url)" 목적으로 API 직접 호출 / assistant write tool / pytest fixture가 있음 → 운영 DB에 non-NULL row 존재 가능.
- pre-check 쿼리 권장: `SELECT COUNT(*) FROM agent_tools WHERE config IS NOT NULL`

### 판단

**[주의 — agent_tools.config drop은 원자적으로 3개 부분을 동시 정리해야 함]**

근거:
1. Merge 로직(`chat_service.py:445`)은 live — 저장된 값이 runtime auth에 실제로 반영된다 (`test_conversations_router.py:280` 증명).
2. 하지만 **쓰기 경로는 프론트엔드에서 사용하지 않음** → UI 회귀 없음.
3. Assistant 내부 `update_tool_config` 도구가 live write — **같이 제거하지 않으면 AI 에이전트 생성 대화에서 AttributeError 발생**.
4. Pydantic 스키마(`ToolConfigEntry`, `AgentUpdate.tool_configs`, `ToolBrief.agent_config`)는 API 외부 contract 변경 — API 클라이언트에 영향.

**M6에서 drop 여부**: **YES — 단, 아래 3개 파일은 반드시 **같은 PR**에 포함**:
1. `backend/app/schemas/agent.py` (ToolConfigEntry / tool_configs / agent_config 필드 제거)
2. `backend/app/services/agent_service.py` (tool_configs 처리 로직 제거)
3. `backend/app/agent_runtime/assistant/tools/write_tools.py`, `read_tools.py` (update_tool_config, get_tool_config, get_agent_config.tools[].config 제거)

pre-migration 데이터 백업 권고(m12 upgrade 전):
```sql
-- 백업: non-null config가 있다면 사용자에게 노출해 재설정 유도
SELECT agent_id, tool_id, config FROM agent_tools WHERE config IS NOT NULL;
```

---

## scope creep 차단 체크리스트

- [x] 옵션 D (PATCH /api/tools/{id} connection_id) 관련 변경 **0건** — PATCH `/auth-config` 엔드포인트 삭제는 기존 dead API 정리일 뿐 옵션 D 아님. 옵션 D의 새 `connection_id` 파라미터 추가는 M6.1.
- [x] `ConnectionBindingDialog` / `triggerContext` 관련 로직 변경 **0건** — `useUpdateMCPServer` 사용처는 dead API 정리 목적의 필수 최소 삭제만, binding dialog 리팩토링은 M6.1.
- [x] M5.5 (`agent_tools.connection_id` override) 관련 변경 **0건**
- [x] 백엔드 신규 기능 **0건** (m12 migration만 추가)
- [x] 프론트엔드 신규 기능 **0건**

**⚠ scope 경계 fuzzy**:
- `frontend/src/app/tools/page.tsx` 의 MCP 섹션 제거는 CHECKPOINT.md §S4 "thin cleanup"을 초과할 가능성. **저커버그 S4에서 별도 판단 필요** — 타입/API 삭제로 빌드 에러 나는 곳만 최소 수정 vs 전체 MCP 섹션 drop.
- `PATCH /api/tools/{tool_id}/auth-config` 라우터 삭제 여부 — 기술적으로 dead이지만 삭제는 API contract 변경. 보수 판정: **유지 + internal no-op 처리** 가능하나, 권장은 **삭제** (M6 목표는 "legacy 전체 제거").

---

## 젠슨에게 주는 지시 (S3 input)

### 1. m12 migration 작성 순서 (upgrade)

피차이가 S2에서 상세 스펙을 확정하겠지만 권장 순서:
```
1. PRE-CHECK (warning only):
   SELECT COUNT(*) FROM tools WHERE credential_id IS NOT NULL AND connection_id IS NULL
   SELECT COUNT(*) FROM tools WHERE mcp_server_id IS NOT NULL AND connection_id IS NULL
   SELECT COUNT(*) FROM agent_tools WHERE config IS NOT NULL
2. FK drop:
   ALTER TABLE tools DROP CONSTRAINT fk_tools_credential_id ...
   ALTER TABLE tools DROP CONSTRAINT fk_tools_mcp_server_id ...
3. Column drop:
   ALTER TABLE tools DROP COLUMN credential_id, auth_config, mcp_server_id
   ALTER TABLE agent_tools DROP COLUMN config
4. Table drop:
   DROP TABLE mcp_servers   -- credential FK ondelete=SET NULL 이므로 단순 drop
```
downgrade: 구조 복구만, 데이터 복구 불가 (progress.txt 이미 명시).

### 2. `_resolve_legacy_tool_auth` 처리 방식

**완전 삭제**. 모든 호출처:
- PREBUILT `provider_name IS NULL` 분기 (chat_service.py:433) — **분기 자체 제거**, PREBUILT는 provider_name 강제. provider_name NULL row는 m10에서 이미 백필되었어야 함 (progress.txt: "m10 백필 실패 row" = 이론상 0).
- CUSTOM `connection_id IS NULL` 분기 (chat_service.py:319) — **ToolConfigError raise**로 변경. M4/M5에서 모든 CUSTOM tool이 connection 경유로 이관됨 (m11 migration).
- BUILTIN else (chat_service.py:441-443) — BUILTIN은 auth 필요 없음 → `cred_auth = {}` 로 변경. 혹시 legacy BUILTIN 중 credential 쓰는 것 있는지 검색 필요 — 없으면 완전 제거.

**PRE-CHECK 미충족 시 migration 실패 처리**: `credential_id IS NOT NULL AND connection_id IS NULL` row가 존재하면 upgrade abort하고 수동 이관 요구. 이미 CHECKPOINT.md §리스크 #3에 명시.

### 3. 테스트 편집 가이드라인

핵심 3가지:

**(a) Import/fixture sweep 먼저**
- 모든 테스트 파일에서 `from app.models.tool import ..., MCPServer, ...` → `MCPServer` 제거하고 grep 0 확인
- `AgentToolLink(... config=...)` → `AgentToolLink(...)` 일괄 삭제. ripgrep 회차:
  ```
  rg "AgentToolLink\([^)]*config=" backend/tests/
  ```
- `Tool(... auth_config=...)` / `Tool(... credential_id=...)` / `Tool(... mcp_server_id=...)` 모두 제거. PREBUILT tool fixture는 connection 경유로 재작성.

**(b) connection path 테스트 **유지 필수****
- `test_connection_mcp_resolve.py` / `test_connection_custom_resolve.py` / `test_connection_prebuilt_resolve.py` 의 connection-path `[K]` 테스트는 m12 이후 **반드시 통과해야 M6 ready**. 삭제하지 말 것.
- m9/m10/m11 migration helper 테스트도 유지 (downgrade 불가 선언이 있어도 helper contract는 여전히 유효).

**(c) scope 엄수**
- `test_agent_tool_config.py` 파일 전체 삭제는 agent_tools.config drop 확정 시에만. 만약 사용자가 "agent_tools.config 유지"로 scope 변경하면 이 파일도 유지해야 함.
- `test_tools.py`, `test_tools_router_extended.py`에서 `[D]` 표시된 MCP 전용 테스트만 삭제. CUSTOM / provider_name / connection_id 관련 테스트는 grep 으로 재확인 후 유지.

### 추가 권고

- **`PATCH /api/tools/{tool_id}/auth-config` 라우터 삭제 여부는 사티아와 사전 합의**. M6 스코프 외로 남기면 dead code이되 안전. 삭제 권고하나 최종 결정은 사티아.
- **`ToolCustomCreate.auth_config`/`credential_id` 필드** 삭제 범위 — 프론트가 이 필드를 여전히 보내는지 grep 필수. `frontend/src/lib/types/index.ts:359` 참조로 보낼 가능성 있음 (CUSTOM 생성 시 legacy auth 경로). 확인 후 결정.
- **cascade 경로 검증**: m12 upgrade 후 `uv run pytest` full run에서 `AttributeError: 'Tool' object has no attribute 'auth_config'` 류 에러가 0이어야 함. grep:
  ```
  rg "\.auth_config|\.credential_id|\.mcp_server(_id)?|\.mcp_server\b" backend/app/
  ```
  이것을 S3 끝에 **반드시** 실행.

---

## 검증 체크

- [x] 파일:라인 단위 제거 목록 완비
- [x] agent_tools.config safety 판단 — 3-파트 동시 정리 요구 명시
- [x] 테스트 유지/삭제 함수별 표
- [x] scope creep 차단 체크리스트
- [x] 젠슨 S3 가이드 3개 핵심
