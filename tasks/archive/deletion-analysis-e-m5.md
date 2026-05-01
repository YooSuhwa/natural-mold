# S1 삭제 분석 보고서 — 백로그 E M5

**작성자**: 베조스
**일자**: 2026-04-19
**스코프**: 프론트엔드 전용 (백엔드 0건 수정)
**근거**: CHECKPOINT.md §S1, ADR-008, exec-plan §4 M5

---

## Working-Backwards 요약

사용자 관점에서 M5가 끝난 세상:
- `/connections` 진입 → Connection 카드(PREBUILT/CUSTOM/MCP 섹션) 1급, Credential 카드 직접 노출 0.
- "연결 추가"를 누르면 어디서든 **같은 ConnectionBindingDialog**가 열린다 (3종 표면 → 1).
- 도구 카드의 "인증" 버튼, `/add-tool` MCP 탭의 "등록" 버튼도 같은 셸로 수렴.
- 백엔드는 M4 상태 그대로 (`connections` 테이블 + `agent_tools.connection_id` 없이).

**M5.5(추후)**: agent-level override (`agent_tools.connection_id`) — 본 M5에서 **절대 미접촉**.
**M6(추후)**: legacy 컬럼 drop (`tool.credential_id`, `tool.auth_config`, `tool.mcp_server_id`, `agent_tools.config`, `mcp_servers` 테이블) — 본 M5에서 **미접촉**.

---

## 1. 즉시 삭제 (M5에서 바로 제거)

### 1.1 `frontend/src/app/connections/page.tsx` — Credential 카드 표면
| 대상 | 위치 | 사유 |
|------|------|------|
| `CredentialCard` 컴포넌트 | `page.tsx:302-368` | Credential은 Connection 상세 drawer 안에서만 노출. 1급 카드로 나열 금지 (ADR-008 재편 방향) |
| `filteredCredentials`/`search`/`typeFilter` | `page.tsx:64-84, 108-134` | Credential 검색/필터는 Connection 중심 페이지에서 의미 없음. Connection 자체 검색으로 대체 (S4에서 재구성) |
| `deletingTarget` + `AlertDialog` delete 흐름 | `page.tsx:67, 172-199` | Credential 삭제는 Connection 상세에서 수행 (연결 해제 포함 UX) |
| `openCreate`/`openEdit` 단독 진입 | `page.tsx:86-94, 130-134, 166-170` | "연결 추가" CTA → ConnectionBindingDialog로 통합 |
| `useCredentialProviders`/`getProviderLabel` 상위 사용 | `page.tsx:59, 99-102, 148` | Credential 카드 제거 후 같은 데이터는 Connection 상세에서 참조 (re-use로 이동) |

**근거**: CHECKPOINT §스코프 합의 "Credential 카드 제거, Connection이 1급".
**주의**: `useCredentials`/`useDeleteCredential` **hook 자체는 유지** — Connection 상세 drawer가 재사용한다 (아래 §3 보류 대상과 구분).

### 1.2 호출부: `tools/page.tsx` 의 PREBUILT 분기
| 대상 | 위치 | 처리 |
|------|------|------|
| `PrebuiltAuthDialog` import + 호출 | `app/tools/page.tsx:30, 241-249` | `ConnectionBindingDialog(type='prebuilt', providerName=tool.provider_name)` 직접 호출로 교체. trigger prop 대신 open state로 전환 (S3) |
| `PrebuiltAuthDialog` 파일 자체 | `components/tool/prebuilt-auth-dialog.tsx` (60줄) | 현재 thin wrapper. legacy fallback 한 줄(`!isPrebuiltProviderName → CustomAuthDialog`)만 남아 있음. 제거 시 fallback 경로 소멸 → M6 legacy drop과 함께 처리해야 안전하므로 **파일은 M6까지 유지**, 호출부만 교체 |

**결정**: 파일은 M6까지 thin wrapper로 남기되, `tools/page.tsx` 호출부를 `ConnectionBindingDialog` 직접 호출로 옮겨 **M5의 "3 dialog → 1" 흡수 검증이 grep으로 통과**하게 한다.

---

## 2. 단순화/대체 (ConnectionBindingDialog로 흡수)

### 2.1 `CustomAuthDialog` (components/tool/custom-auth-dialog.tsx, 108줄)
- **현재 표면**: `CredentialSelect` + `CredentialFormDialog` + `useUpdateToolAuthConfig({credentialId})` — `tool.credential_id` 직접 편집.
- **M5 처리**: ConnectionBindingDialog에 `type='custom'` 분기 추가 (find-or-create connection, CUSTOM `provider_name='custom_api_key'` scope).
- **호출부 교체**: `app/tools/page.tsx:252-260` — CustomAuthDialog → ConnectionBindingDialog(type='custom', tool).
- **주의(M4 bridge)**: CustomAuthDialog는 오직 "기존 tool의 credential 교체"만 수행 — connection_id를 변경하지 않음. 교체 후에도 chat_service `_resolve_custom_auth`가 `tool.connection_id`에서 credential을 derive하므로 **connection_id가 이미 세팅된 tool의 credential 교체**는 connection.credential_id PATCH로 해결 (ADR-008 N:1).
- **파일 자체 유지 여부**: CustomAuthDialog는 `PrebuiltAuthDialog`의 legacy fallback(§3)에서도 참조된다. **파일은 M6까지 유지**, 호출부만 교체.

### 2.2 `MCPServerAuthDialog` (components/tool/mcp-server-auth-dialog.tsx, 110줄)
- **현재 표면**: `server.credential_id` + `useUpdateMCPServer({credential_id})` — mcp_servers row의 credential FK 직접 편집.
- **M5 처리**: ConnectionBindingDialog에 `type='mcp'` 분기 추가. 단, backend `mcp_servers` 테이블/`useUpdateMCPServer` API는 **M6까지 유지**.
- **호출부 교체**: `components/tool/mcp-server-group-card.tsx:28, 143`.
- **비대칭성**: MCP는 PREBUILT/CUSTOM과 달리 **server 엔티티(URL+name)가 선행**하고 credential은 2차 속성. M5에서는 "인증" 진입 시 **credential binding만** ConnectionBindingDialog로 흡수하고, server create/rename/delete는 그대로 둔다. 즉 `type='mcp'`는 mcp_server_id를 외부 context로 받아 credential만 교체하는 셸로 설계.
- **risk**: MCP connection 엔티티 자체가 M4에서 존재하는지 확인 필요 — exec-plan §4 M5에서 "server config 입력"까지 명시되어 있으나, ADR-008 상 MCP connection = 1 server, credential은 server의 속성. S2에서 팀쿡 spec으로 확정.

### 2.3 `add-tool-dialog` MCP 탭 (components/tool/add-tool-dialog.tsx, 383줄)
- **현재 흐름**: form(name, url, credential_id) → `useRegisterMCPServer` → discoveredTools 표시.
- **M5 처리**: MCP 탭 본체는 유지하되 credential select 영역을 **ConnectionBindingDialog 진입 버튼**으로 재배선 OR 현재 CredentialSelect를 유지하고 "새 credential 생성"을 ConnectionBindingDialog(type='mcp')로 통합.
- **권장**: MCP tab은 "신규 server 등록" 목적이므로 ConnectionBindingDialog 통합은 과잉. CredentialSelect 그대로 두고 **"새 credential 만들기" CTA만** ConnectionBindingDialog로 수렴 — 저커버그 S3에서 판단. 
- **Custom 탭**: M4에서 이미 connection find-or-create로 전환됨 (add-tool-dialog.tsx:96-116). **변경 없음**. 단, `credential_id: customCredentialId` bridge 전송 라인(157-160)은 §3 보류(M6).

---

## 3. 보류 M6 — Legacy Drop 대상 (M5에서 절대 미접촉)

### 3.1 `tool.credential_id` 기반 코드
| 파일 | 라인 | 보류 사유 |
|------|------|-----------|
| `components/tool/prebuilt-auth-dialog.tsx` | 33-35 | provider_name NULL fallback → CustomAuthDialog 위임. backend legacy fallback과 1:1 대응. M6에서 fallback 경로 일괄 제거 시 동시 삭제 |
| `components/tool/custom-auth-dialog.tsx` | 16, 36, 42-46 | `useUpdateToolAuthConfig`로 `tool.credential_id`를 직접 편집. backend `tool.credential_id` 컬럼 drop과 묶여야 안전 |
| `components/tool/add-tool-dialog.tsx` | 153-160 | Custom tool create 시 `credential_id` 필드 함께 전송 (bridge). "M5에서 consumer 전환 후 제거" 주석 있으나, consumer = `tools/page.tsx` getAuthStatus도 `tool.credential_id` 의존 — **모두 M6에서 일괄 제거**가 안전 |
| `app/tools/page.tsx` | `getAuthStatus`(미열람 구간) | Custom tool "configured" 판정이 `tool.credential_id` 기반. Connection 기반으로 전환 시 backend API 응답에 connection 요약 포함 필요 여부 확인 — **M6 scope** |

### 3.2 `tool.auth_config` (inline auth)
- **보류**: M6 legacy drop 대상. `components/tool/custom-auth-dialog.tsx:42-45`의 `useUpdateToolAuthConfig({authConfig: {}, credentialId})` 호출이 `auth_config`를 비우는 경로. M6에서 컬럼과 함께 제거.

### 3.3 `tool.mcp_server_id` & `mcp_servers` 테이블
- **보류**: `lib/api/` 내 mcp_server 관련 API, `useRegisterMCPServer`, `useUpdateMCPServer`, `useDeleteMCPServer`, `mcp-server-group-card.tsx`, `mcp-server-rename-dialog`, `mcp-server-auth-dialog.tsx` 전체. M6에서 Connection(type='mcp')로 server 엔티티 통합 후 일괄 제거.
- **M5 허용**: M5.2 호출부 교체(§2.2)는 표면 변경만 — `useUpdateMCPServer({credential_id})` 호출 자체는 유지 (ConnectionBindingDialog 내부에서 호출하거나 별도 mutation hook로 우회).

### 3.4 `agent_tools.config` JSON
- **보류**: agent별 도구 설정 JSON. M5 스코프 외. agent 편집 페이지에서 사용 중이면 M6까지 유지.

### 3.5 `PrebuiltAuthDialog`/`CustomAuthDialog`/`MCPServerAuthDialog` 파일 자체
- **보류**: §1.2, §2.1, §2.2 결정에 따라 **호출부만 교체**, 파일은 M6까지 thin wrapper로 유지. F 흡수 검증은 grep 호출부 0으로 판정 (CHECKPOINT §S3 마지막 체크리스트 기준으로 OK).

---

## 4. 보류 M5.5 — agent_tools.connection_id override (M5에서 절대 미접촉)

### 현재 코드베이스 스캔 결과
- **agent_tools.connection_id 컬럼 미존재** (M4 상태). grep `connection_id.*agent_tools` → 0.
- **agent 도구별 connection override UI 미존재**. `app/agents/[id]/config/*`는 agent_tools.config JSON 기반 설정만 다룸.
- **결론**: M5.5는 backend m12 + chat_service 분기 + 신규 UI 전부 **신규 작업**. M5에서 건드릴 기존 코드 없음.

**M5에서의 행동**: `frontend/src/app/agents/[id]/config/**`, `backend/app/services/chat_service*`, `backend/app/routers/agent_tools*`, `backend/alembic/versions/**` — **touch 금지**.

---

## 5. 신규 작업 (M5에서 생성할 항목 — 삭제 대상 아니지만 맥락)

- `components/connection/connection-binding-dialog.tsx` — 이미 M3에서 PREBUILT 전용으로 존재 (237줄). M5 S3에서 `type` discriminated union으로 확장 (prebuilt | custom | mcp).
- `messages/ko.json` `connection.binding.{custom,mcp}.*` 키 추가 (기존 prebuilt 섹션 있음).
- Connection 상세 drawer/modal (S4) — `credentials` hook 재사용.

---

## 6. 검증 체크리스트 (M5 완료 시 재확인)

```bash
# F 흡수: 3 AuthDialog 호출부가 0이어야 함 (파일은 thin wrapper로 남음)
rg -n "PrebuiltAuthDialog|CustomAuthDialog|MCPServerAuthDialog" frontend/src/app frontend/src/components --glob '!*auth-dialog.tsx'
# 기대: 0 hits

# ConnectionBindingDialog 호출처 ≥ 3 (tools/page.tsx × 2, mcp-server-group-card.tsx × 1, connections/page.tsx × 1)
rg -l "ConnectionBindingDialog" frontend/src

# 백엔드 0건
git diff main...HEAD -- backend/ | wc -l
# 기대: 0

# agent_tools / chat_service / alembic 0건
git diff main...HEAD -- 'backend/app/services/chat_service*' 'backend/app/routers/agent_tools*' 'backend/alembic/' | wc -l
# 기대: 0
```

---

## 7. 위험 신호 (사티아에게 공유)

1. **PrebuiltAuthDialog 파일 존치 vs 삭제 판단**: 현재 thin wrapper + legacy fallback이 backend fallback과 1:1 대응. 삭제하면 legacy provider_name NULL tool이 "관리 불가"가 됨. **권장: 파일 유지, 호출부만 교체**.
2. **CustomAuthDialog의 connection semantics**: "credential만 교체" → connection.credential_id PATCH로 해결 가능. 단, M4 bridge override 흐름(`tool.credential_id != connection.credential_id`)을 사용자가 이미 만들어 놓은 row가 있다면 PATCH 방향이 bridge를 덮어쓸 수 있음 — **S3 구현 시 팀쿡 spec에서 "기존 bridge row는 어떻게 처리할 것인가" 명시 필요**.
3. **MCP server + credential 분리 UX**: ConnectionBindingDialog(type='mcp')가 server_id를 props로 받아야 한다. 신규 server create는 여전히 add-tool-dialog에서 수행 — UX가 두 군데로 쪼개짐. 팀쿡 S2에서 통합 vs 분리 결정 필요.
4. **tools/page.tsx getAuthStatus**: 본 분석에서 파일 상단(~30-220)은 미열람. `tool.credential_id` 기반 "configured" 판정이 있다면 CustomAuthDialog 교체 후에도 UI 회귀 없이 동작하는지 **S3 구현 중 확인**.
5. **Credential hook 재사용 경계**: `useCredentials` 자체는 Connection 상세 drawer에서 재사용. 하지만 `useDeleteCredential`은 Connection 삭제와 혼동 위험 — "Connection 삭제 = Credential 삭제"인지 "Connection 삭제 ≠ Credential 삭제(다른 connection이 참조 가능)"인지 spec 명시 필요.

---

## 8. 분류 요약표

| 카테고리 | 건수 |
|----------|------|
| 즉시 삭제 (M5) | 5개 (connections/page.tsx Credential 카드 섹션 관련) + 호출부 교체 3건 |
| 단순화/대체 (M5) | 3개 dialog 호출부 → ConnectionBindingDialog 흡수 |
| 보류 M6 | legacy 컬럼 4종 + 파일 3개(thin wrapper) + mcp_servers API 일체 |
| 보류 M5.5 | 없음 (신규 기능, 기존 코드 미변경) |
| 백엔드 변경 | **0건** |

**Bezos "?"**: tools/page.tsx `getAuthStatus`의 Custom tool 판정 로직이 `tool.credential_id` 의존이면, S3 구현 후 "Custom 도구가 connection 바인딩되어도 '미설정'으로 표시" 회귀 가능 → 저커버그 구현 시 확인할 것.
