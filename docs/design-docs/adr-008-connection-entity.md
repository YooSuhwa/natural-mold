# ADR-008: Connection 엔티티 — Credential 바인딩 통합

## 상태: 제안됨

## 날짜: 2026-04-18

## 맥락

현재 도구(Tool)-자격증명(Credential) 바인딩은 타입별로 위치와 시맨틱이 다르고, PREBUILT 시스템 도구에서 유저별 분리가 불가능하다.

### 문제 1 — PREBUILT 공유 행의 credential 뒤엉킴

시스템 도구(`is_system=True`)는 `user_id=NULL`인 공유 행이다. `tool.credential_id`에 유저 A의 credential을 박으면, 유저 B가 같은 도구를 조회·실행할 때 A의 credential이 보이거나 사용된다. 현재 PoC 단계에선 mock user 1명이라 드러나지 않지만, 멀티 유저 인증을 도입하는 순간 **프로덕션 incident**로 전환된다.

### 문제 2 — 바인딩 위치 비일관

| 타입 | credential 바인딩 위치 |
|------|----------------------|
| MCP | `mcp_servers.credential_id` (서버 단위) |
| CUSTOM | `tool.credential_id` (도구 단위) |
| PREBUILT | `tool.credential_id` (공유 행 단위 — 문제 1) |

유저 입장에선 "이 도구에 내 키가 어디 붙어 있는지"를 세 곳을 봐야 하고, 해석 로직도 `chat_service.build_tools_config`에서 3분기로 나뉜다. 프론트 다이얼로그(`prebuilt-auth-dialog`, `custom-auth-dialog`, `mcp-server-auth-dialog`)도 90% 동일한데 저장 경로 때문에 3개로 분리돼 있다 (백로그 F).

### 문제 3 — 해석 경로 중복과 override 시맨틱 불명

`agent_tools.config` JSON이 도구의 `auth_config`와 shallow merge 되는 관행이 있고, `credential_id`와 inline `auth_config` fallback이 공존해 우선순위가 모호하다. env var fallback(`settings.naver_*`)도 경로에 섞여 "왜 이 키가 써졌는지" 추적이 어렵다.

---

## 결정

`connections` 엔티티를 신규 도입해 **유저×도구 타입×provider** 수준에서 credential 바인딩을 통일한다. Tool은 "무엇을 호출할지(정의)", Connection은 "누가 어떤 키로 호출할지(바인딩)"로 관심사를 분리한다.

### 1. 스키마

```sql
connections
  id               UUID  PK
  user_id          UUID  FK users      NOT NULL                 -- 권한 분리의 기반
  type             VARCHAR(20)  NOT NULL                        -- 'prebuilt' | 'mcp' | 'custom'
  provider_name    VARCHAR(50)  NOT NULL                        -- PREBUILT: credential_registry enum 5종
                                                                -- MCP/CUSTOM: 자유 문자열
  display_name     VARCHAR(200) NOT NULL                        -- UI 레이블 (중복 허용)
  credential_id    UUID  FK credentials (nullable, ON DELETE SET NULL)
  extra_config     JSON  nullable                               -- MCP만 사용 (아래 구조)
  is_default       BOOLEAN NOT NULL DEFAULT false               -- (user_id, type, provider_name) 당 1개
  status           VARCHAR(20)  NOT NULL DEFAULT 'active'       -- 'active' | 'disabled'
  created_at       TIMESTAMP NOT NULL
  updated_at       TIMESTAMP NOT NULL

  INDEX (user_id, type, provider_name)                          -- hot path: 도구 해석 시 조회
```

- **UNIQUE 제약 없음**. UUID PK가 고유성을 보장하고, 같은 provider에 대해 "회사용/개인용"처럼 display_name이 동일/유사한 복수 connection을 허용 (업계 표준: GitHub, Slack).
- `provider_name` validator는 Python/Pydantic 레벨에서 type에 따라 분기:
  - `type='prebuilt'`: `credential_registry` enum (`naver`, `google_search`, `google_workspace`, `google_chat`, `custom_api_key`)만 허용
  - `type='mcp'` / `type='custom'`: 자유 문자열 (영문/숫자/언더스코어, 길이 제한)

### 2. `extra_config` 구조

**MCP만 사용**. PREBUILT/CUSTOM은 NULL.

```json
{
  "url": "https://example.com/mcp",
  "auth_type": "none | bearer | api_key | oauth2 | basic",
  "headers": { "X-Custom": "value" },
  "env_vars": { "RESEND_API_KEY": "${credential.api_key}" },
  "transport": "http | stdio",
  "timeout": 30
}
```

- `url`, `auth_type`은 필수.
- `headers`, `env_vars`, `transport`, `timeout`은 선택.
- `env_vars` 값은 **plain string** 또는 **credential 필드 참조 템플릿** (`${credential.field_name}`). 비밀값은 credential에 저장하고 env_vars는 참조만 (유사 서비스 UX 참고 — 이미지 5의 `${RESEND_API_KEY}` 문법). **템플릿 해석 로직의 실제 구현은 M2 MCP 실행 경로에서 수행**하며, M1은 스키마만 허용한다.

### 3. 도구 타입별 해석 로직

```python
# chat_service.build_tools_config (신규 경로)

if tool.type == PREBUILT:
    conn = get_connection(user_id, type='prebuilt', provider_name=tool.provider_name, is_default=True)
    cred_auth = resolve_credential_data(conn.credential) if conn and conn.credential else {}
    # env fallback 없음 (결정 4 참조)

elif tool.type == MCP:
    conn = get_connection_by_id(tool.connection_id)  # tool.connection_id (M2 신규 FK)
    cred_auth = resolve_credential_data(conn.credential)
    mcp_url = conn.extra_config['url']
    mcp_auth_type = conn.extra_config['auth_type']
    # headers, env_vars 등 MCP 실행 경로에서 처리

elif tool.type == CUSTOM:
    conn = get_connection_by_id(tool.connection_id)  # tool.connection_id (M4 신규 FK)
    cred_auth = resolve_credential_data(conn.credential)

# agent_tools.connection_id (override) 있으면 해당 connection 사용
if link.connection_id is not None:
    conn = get_connection_by_id(link.connection_id)
    cred_auth = resolve_credential_data(conn.credential) if conn.credential else cred_auth
```

- `agent_tools.connection_id` (nullable FK): 에이전트별로 기본 connection 대신 다른 것을 쓰고 싶을 때 override.
- `agent_tools.config` (기존 JSON)은 M6에서 drop.

### 4. env fallback 정책

**유저 도구 실행 경로에선 env fallback 제거**. `settings.naver_*` 등을 credential 해석 시점에 읽지 않는다.

**근거**:
- env 키는 개인 귀속이 안 되어 감사/비용 배분 불가
- "마법처럼 동작"하다 env 제거 시 갑자기 깨지는 은닉 버그 발생
- 멀티 유저 인증 도입 후에도 env를 모든 유저의 암묵적 기본값으로 두는 건 보안 원칙 위반

**예외 — 시스템 내부 기능**:
- `creation_agent` (대화형 에이전트 생성 메타 에이전트)
- 에이전트 카드 이미지 생성
- 기타 유저 귀속이 없는 시스템 작업

이들은 connection 개념을 적용하지 않고 env를 직접 읽는 기존 경로를 유지한다. `config.py`의 `settings.openai_api_key` 등은 LLM 시스템 호출 용도로 존속.

**M3 이행 전략**: 시드된 시스템 도구(`is_system=True`)가 현재 env로 동작 중이므로, M3 마이그레이션 시 mock user에 대해 env 값을 credential로 자동 복사하고 그 credential을 default connection으로 연결한다. env 값이 없으면 connection도 만들지 않고 "연결 안됨" 상태로 표시. 기존 UX 깨짐 없이 전환.

### 5. `is_default` 시맨틱

- 유저가 provider별 첫 connection을 만들면 자동 `is_default=True`
- UI에서 다른 connection을 default로 승격하면 기존 default는 자동 `is_default=False` (트리거 또는 서비스 레벨에서 원자적 처리)
- `agent_tools.connection_id = NULL` → 해당 provider의 default connection 사용
- `agent_tools.connection_id = <uuid>` → override

### 6. 이행(마이그레이션) 전략

| 단계 | Alembic | 역할 |
|------|---------|------|
| M1 | `m8_add_connections` | 테이블 + 인덱스 생성 (아직 아무도 참조 안 함) |
| M2 | `m9_migrate_mcp_to_connections` | `mcp_servers` 각 row → `connections` (type='mcp', extra_config에 url/auth_type 이관) + `tools.connection_id` 컬럼 추가 + `tools.mcp_server_id` 기반 매핑 |
| M3 | `m10_seed_prebuilt_connections` | mock user에 대해 env 값 → credential → default connection 자동 생성. env 없는 provider는 skip |
| M4 | `m11_migrate_custom_credentials` | 기존 `tool.credential_id`가 있는 CUSTOM 도구 → 1 credential = 1 connection, 여러 도구가 N:1로 공유 |
| M6 | `m12_drop_legacy_columns` | `mcp_servers` drop, `tools.credential_id` drop, `tools.auth_config` drop, `tools.mcp_server_id` drop, `agent_tools.config` drop |

- **M2~M5 기간**: legacy 컬럼은 read-only로 유지(코드 경로만 connection 경유로 전환). 언제든 마일스톤 단위로 롤백 가능.
- **Alembic downgrade**: 모든 마이그레이션에 `downgrade()` 구현 필수. CI에서 `upgrade → downgrade → upgrade` 왕복 검증.

---

## 대안

### 대안 A — `agent_tools` 레벨 바인딩만 (Connection 없음)

`agent_tools`에 `credential_id`, `mcp_config` 등을 추가해 에이전트-도구 조합마다 직접 credential을 매달자.

**기각 이유**:
- 같은 유저가 5개 에이전트에서 네이버 검색을 쓰면 credential을 5번 중복 지정해야 함 (재사용 불가)
- provider별 "내 기본 키" 개념이 없어 UX 번거로움
- MCP 서버 설정(URL, 헤더)도 매 agent_tools마다 중복 저장

### 대안 B — `tool.credential_id`에 user_id 추가

`tool` 테이블에 `user_id` 컬럼을 추가해 공유 행을 유저별 행으로 쪼개자.

**기각 이유**:
- 시스템 시드 도구가 유저 수만큼 복제됨 (N배 row inflation)
- 도구 정의(설명, 스키마)와 유저별 바인딩이 섞임 — 관심사 분리 실패
- 시드 업데이트 시 모든 유저 행을 갱신해야 함

### 대안 C — MCP는 `mcp_servers` 유지, PREBUILT만 Connection 도입

MCP는 이미 서버 단위 구조가 있으니 그대로 두고, 문제의 PREBUILT 공유 행만 connection으로 분리.

**기각 이유**:
- 바인딩 위치 비일관 문제(문제 2) 미해결 — 여전히 3곳을 봐야 함
- 프론트 다이얼로그 중복(F) 미해결
- 장기적으로 인증·상태·override 로직을 두 테이블에서 중복 관리

### 대안 D — `credentials`에 `user_id + provider_name` 추가, Connection 없음

credential 자체를 provider 바인딩까지 포함하게 확장.

**기각 이유**:
- credential = 비밀값, connection = 바인딩 메타의 관심사 분리가 깨짐
- 같은 API 키를 "회사용/개인용"처럼 display_name만 달리 쓰는 케이스가 credential 중복 생성으로 풀림 → 비밀값 이중 저장·회전 시 누락 위험
- MCP URL/headers 같은 non-secret 설정을 credential 테이블에 섞어야 함

---

## 결과

### 긍정

- **유저별 credential 분리**: PREBUILT 공유 행 문제 완전 해소. 멀티 유저 인증 도입 시 바로 안전
- **단일 바인딩 경로**: 도구 해석 시 항상 connection을 거침. `build_tools_config` 로직 단순화
- **UI 통합**: 3개 auth 다이얼로그 → `ConnectionBindingDialog` 1개 + context prop (백로그 F 흡수)
- **Agent별 override**: 파워 유저가 에이전트마다 다른 credential 사용 가능
- **Credential 재사용**: 같은 API 키를 여러 CUSTOM 도구에서 공유 (N:1)
- **상태 관리**: `is_default`, `status='disabled'` 같은 connection 레벨 on/off 가능

### 부정

- **테이블 1개 추가 + 중간 테이블 레이어**: tool → connection → credential 간접도 1단계 증가
- **마이그레이션 복잡도**: M2~M5에 걸친 긴 이행 기간. 각 마일스톤마다 legacy와 신규 경로 공존
- **프론트/백 타입 동기화 부담**: 매 마일스톤마다 `lib/types/index.ts` 갱신 필수

### 보안

- `connections.user_id`가 NOT NULL이므로 API 라우트에서 `get_current_user().id`로 필터 필수. PoC 단계에서도 IDOR 회귀 테스트 포함
- credential 자체는 기존 Fernet 암호화(`data_encrypted`) 그대로 유지. connection은 비밀값을 담지 않음
- env var 경로 제거로 "감사 불가한 암묵적 키 사용" 경로 차단
- `extra_config.env_vars` 템플릿 참조(`${credential.xxx}`)는 서버 측에서만 해석. 클라이언트 응답에 실제 비밀값 노출 없음

### 성능

- `INDEX (user_id, type, provider_name)`로 hot path 조회 O(log n)
- 도구 해석 시 join 1단계 증가 (tool → connection → credential) — SQLAlchemy `selectinload`로 N+1 방지
- `is_default` 변경은 같은 (user_id, type, provider_name) 범위 내 UPDATE 2건 — 서비스 레벨 트랜잭션

### 마이그레이션 리스크

- M2 `mcp_servers → connections` 이관 중 데이터 누락: `upgrade()` 후 row 수 assertion
- M3 env → credential 자동 복사 시 ENCRYPTION_KEY 미설정이면 skip + 명확한 경고 (ADR-007 패턴 준수)
- M6 legacy drop 시점에 아직 legacy 경로를 참조하는 코드가 있으면 런타임 에러 — M5 완료 시 grep 전수 검사

---

## 테스트 시나리오

### M1 (신규 `tests/test_connections.py`, 8 시나리오)

1. **CRUD 기본**: 생성 / 조회 / 수정 / 삭제 (credential 연결 + NULL 둘 다)
2. **MCP validator**: `type='mcp'`인데 `extra_config.url`이 없으면 422
3. **PREBUILT validator**: `type='prebuilt'`에 `provider_name='foo'` 같은 non-enum 값은 422
4. **is_default 자동 설정**: 첫 connection 생성 시 `is_default=True` 자동
5. **is_default 토글 원자성**: 기존 default가 있는데 다른 connection을 default로 승격하면 기존은 자동 해제
6. **IDOR 방지**: user_A가 user_B의 connection을 GET/PATCH/DELETE 시도 시 404
7. **credential ON DELETE SET NULL**: credential 삭제 시 connection.credential_id는 NULL이 되고 connection은 살아있음
8. **extra_config 타입 불일치**: PREBUILT에 `extra_config={...}` 주면 경고 또는 무시 (validator 판단)

### M2 (MCP → Connection 이관)

1. **기존 `mcp_servers` row 전체가 `connections` 테이블로 이관되었는지** (row count + 샘플 비교)
2. **tools.connection_id가 기존 tools.mcp_server_id 기반으로 정확히 매핑**
3. **MCP 도구 실행 스모크**: 이관 후 agent가 MCP 도구를 호출 가능
4. **기존 `test_mcp_connection`, `test_tools_router_extended` 회귀 PASS**
5. **Alembic 왕복**: `m9` upgrade → downgrade → upgrade PASS

### M3 (PREBUILT per-user — **E 핵심 검증 포인트**)

1. **다중 유저 격리 (필수)**: mock user 2명(user_A, user_B) 만들고 같은 PREBUILT 도구(예: naver_search)를 각자의 connection으로 실행. 각자 다른 credential이 사용됨을 로그/mock으로 검증 → **공유 행 뒤엉킴 문제 해소 증명**
2. **env → credential 자동 시드**: `.env`에 `NAVER_CLIENT_ID`가 있으면 mock user에 credential + default connection 자동 생성
3. **env 제거 후 회귀**: env 값을 일시 제거해도 connection이 있으면 도구 동작. connection 제거 시 명확한 에러
4. **is_default override**: `agent_tools.connection_id` 지정 시 default 대신 해당 connection 사용

### M4 (CUSTOM Connection 통합)

1. **기존 `tool.credential_id` → connection FK 이관 데이터 무결성**
2. **N:1 credential 공유**: 여러 CUSTOM 도구가 같은 connection을 가리킴 → 실행 시 같은 credential 사용
3. **CUSTOM 도구 실행 회귀**: HTTP 도구 호출 전체 경로 통과

### M5 (UI 통합 + F 흡수)

1. **ConnectionBindingDialog**: 3 context (prebuilt/custom/mcp)에서 각각 올바른 스키마로 렌더
2. **기존 3 다이얼로그 제거 후 agent-browser E2E**: PREBUILT 연결, CUSTOM 생성, MCP 추가 각 플로우 통과
3. **/connections 페이지 재편**: Connection 중심 리스트 + credential이 하위 보조로 표시

### M6 (Cleanup)

1. **legacy 컬럼 drop 후 전체 테스트 PASS**
2. **Alembic 양방향 왕복**
3. **grep 전수**: `mcp_server_id`, `credential_id`가 tool 모델·서비스에 남아 있지 않음

---

## 관련 문서

- 실행 계획: `docs/exec-plans/active/backlog-e-connection-refactor.md`
- 선행 ADR: ADR-007 (credentials field_keys 캐시), ADR-005 (Builder/Assistant)
- 후속 작업: 멀티 유저 인증 도입 (E 완료 후 별도 ADR)
- 유사 서비스 UX 참고: MCP `JSON 가져오기` (Claude Desktop mcpServers config import) — M5 UX 확장 후보로 footnote
