# 삭제 분석 보고서 — 백로그 E M1 (Connections 테이블 + CRUD API)

**브랜치**: `feature/connections-table`
**작성자**: 베조스 (QA)
**작성일**: 2026-04-18
**ADR**: `docs/design-docs/adr-008-connection-entity.md`
**스코프 정의**: M1은 `connections` 테이블을 **parallel run** 상태로 신규 추가하는 것만이 목표. 기존 시스템(chat_service, executor, tools, mcp_servers) 영향도 0. 실제 기존 코드 삭제는 M6 `m12_drop_legacy_columns`의 책임.

---

## 분석 원칙

ADR-008 §이행 전략(M1~M6 마이그레이션 테이블) + `progress.txt` 파일 경계를 기준으로 M1에서 "제거 또는 단순화"할 수 있는 항목을 식별한다. M1은 신규 추가 전용 마일스톤이므로 **drive-by 삭제는 금지**. 발견한 legacy 자산은 모두 후속 마일스톤 티켓으로 분리한다.

---

## 즉시 삭제 가능

**0건.**

M1 스코프(`connections` 테이블 신설 + CRUD API + 신규 테스트)는 추가만 다루며, 기존 파일을 참조하거나 대체하지 않는다. 따라서 이 PR에서 안전하게 지울 수 있는 코드는 존재하지 않는다.

---

## 삭제 검토 필요 (후속 마일스톤으로 이월)

아래 항목은 **connection 도입과 장기적으로 중복·불필요**해지지만, ADR-008 §이행 전략에서 M2 이후에 단계적으로 제거하기로 이미 합의된 자산이다. M1에서는 건드리지 않는다.

1. **`backend/app/models/tool.py:34-57` — `MCPServer` 테이블 전체**
   - ADR-008 §이행 전략 M2 (`m9_migrate_mcp_to_connections`): 각 row → `connections(type='mcp', extra_config={url,auth_type,headers,env_vars,...})`로 이관. `url`, `auth_type`, `auth_config`, `credential_id` 모두 connection으로 이동.
   - 실제 drop: **M6** (`m12_drop_legacy_columns`). M1에서는 read-only 유지.
   - 권고: **M1 스코프 외.** 건드리지 말 것.

2. **`backend/app/models/tool.py:75-77` — `Tool.credential_id` FK**
   - ADR-008 §이행 전략 M4 (`m11_migrate_custom_credentials`): CUSTOM 도구의 기존 `tool.credential_id` → 1 credential = 1 connection으로 이관, 여러 도구가 N:1로 공유.
   - 실제 drop: **M6**.
   - 권고: **M1 스코프 외.**

3. **`backend/app/models/tool.py:67` — `Tool.mcp_server_id` FK**
   - ADR-008 §이행 전략 M2에서 `Tool.connection_id` 컬럼을 새로 추가하고 `mcp_server_id` 기반으로 매핑 후, M6에서 drop.
   - 권고: **M1 스코프 외.**

4. **`backend/app/models/tool.py:73-74` — `Tool.auth_type`, `Tool.auth_config`**
   - PREBUILT/CUSTOM 도구의 inline auth 설정. connection 도입 후 해석 경로에서 제거됨 (ADR §3, §이행 전략 M6 drop 목록).
   - 권고: **M1 스코프 외.**

5. **`backend/app/models/tool.py:16-31` — `AgentToolLink.config` JSON**
   - ADR-008 §3 문말: "`agent_tools.config` (기존 JSON)은 M6에서 drop". `agent_tools.connection_id` (M2 추가)로 대체.
   - 권고: **M1 스코프 외.**

6. **프론트엔드 3개 auth 다이얼로그** (`prebuilt-auth-dialog`, `custom-auth-dialog`, `mcp-server-auth-dialog`)
   - ADR §결과 "UI 통합": `ConnectionBindingDialog` 1개 + context prop로 M5에서 흡수 (백로그 F).
   - 권고: **M1 스코프 외.** M5 티켓에서 처리.

7. **`backend/app/services/credential_service.py:110-119` — `resolve_server_auth`**
   - 현재: `MCPServer` → credential 또는 inline auth_config의 우선순위 해석 헬퍼.
   - connection 경유 해석으로 전환되면(M2) 단계적으로 dead path가 된다. 다만 M2 이관 중에도 legacy 경로가 공존하므로 **M2 완료 후까지는 유지** — 최종 제거는 M6 legacy drop과 동시.
   - 권고: **M1 스코프 외.** M6 cleanup 체크리스트에 포함.

8. **`backend/app/services/credential_service.py:122-142` — `get_usage_count`의 `tool_count` / `mcp_server_count`**
   - connection 도입 후 유저 관점 "어디서 쓰이는지"는 connection 사용량 기준으로 재계산해야 자연스럽다. 다만 legacy 컬럼이 drop되는 M6 전까지는 두 count 모두 의미 있음.
   - 권고: **M1 스코프 외.** M5/M6에서 `connection_count` 추가 후 재설계.

---

## 단순화 제안

1. **`connections.provider_name` validator — `CREDENTIAL_PROVIDERS` 재사용 (피차이용 가이드)**
   - 제안: `backend/app/schemas/connection.py`의 `ConnectionCreate` `provider_name` Pydantic validator에서 `type='prebuilt'` 분기를 `set(CREDENTIAL_PROVIDERS.keys())`로 체크. 문자열 enum을 **별도로 정의하지 말 것.**
   - 근거: `credential_registry.py:11`의 `CREDENTIAL_PROVIDERS` dict가 이미 진실 기준(SOT). connection 전용 enum을 복제하면 두 곳에서 5종을 유지해야 하는 drift 위험이 생긴다.
   - 조치: 피차이가 S2에서 직접 import — `from app.services.credential_registry import CREDENTIAL_PROVIDERS`.

2. **MCP `extra_config` validator — ADR 명시 필드만 강제 (피차이용 가이드)**
   - 제안: `extra_config`는 `url`, `auth_type`만 required로 강제. `headers`, `env_vars`, `transport`, `timeout`은 optional. **템플릿 해석(`${credential.xxx}`)은 M1에서 구현하지 말 것** (ADR §2 마지막 문단 — "M2 MCP 실행 경로에서 수행").
   - 근거: M1 스코프는 "스키마만 허용". 템플릿 해석 로직이 지금 들어오면 M2 실행 경로 변경 시 중복 제거 비용 발생.
   - 조치: 피차이가 `extra_config` validator에서 타입/필수키 체크만. 런타임 해석 금지.

3. **`ConnectionResponse`는 decrypted 데이터 반환 금지 (피차이/젠슨 공통)**
   - 제안: `ConnectionResponse`에 `credential_id: UUID | None`만 노출. credential 본체(`data`)는 기존 `CredentialResponse`에서 이미 비노출 원칙 (`credential_service._to_response`가 `resolve_credential_data` 호출 안 함). Connection도 동일 원칙.
   - 근거: ADR-008 §보안 — "클라이언트 응답에 실제 비밀값 노출 없음". `extra_config.env_vars`도 템플릿 값 그대로 반환 (M1에서는 해석 없음 = 안전).
   - 조치: 피차이가 `ConnectionResponse` 정의 시 credential 객체 embed 금지. id만.

4. **테스트 격리 — 기존 `test_credentials.py` / `test_tools.py` 변경 금지 (베조스 본인 가이드)**
   - 제안: S4 `test_connections.py`는 신규 파일 단일. 기존 회귀 스위트(545+) 수정 일절 없음.
   - 근거: M1이 parallel run이므로 기존 동작 시맨틱 0 변화 — 기존 테스트가 실패하면 그것 자체가 regression 시그널이다. 기존 테스트를 고쳐 PASS를 맞추면 regression을 감춘다.
   - 조치: S4 구현 시 `tests/conftest.py`, `tests/test_credentials.py` 등은 읽기만. IDOR 패턴은 참고만.

5. **Alembic `m8_add_connections` downgrade 완전성 (젠슨용 가이드)**
   - 제안: `downgrade()`에서 인덱스 drop → 테이블 drop 순. SQLite 호환을 위해 인덱스 drop은 `op.drop_index(..., table_name="connections")` 명시.
   - 근거: 백로그 C progress.txt 학습 — SQLite에서 `op.batch_alter_table`/`drop_index` 명시 필요. 왕복 검증(`upgrade → downgrade → upgrade`) 필수.
   - 조치: 젠슨이 S3 작성 시 upgrade/downgrade 모두 테스트하고 CHECKPOINT 검증 커맨드 통과 확인.

---

## 보류 / 스코프 외 (기록용)

다음 항목은 보았으나 **명시적으로 M1 스코프 외**로 분류해 이월:

- `backend/app/routers/credentials.py` 전체 — connection 도입과 무관, 변경 없음.
- `backend/app/models/credential.py` — credential 자체는 그대로 유지 (ADR §결정). `user_id`, 암호화, field_keys 캐시(ADR-007) 모두 존속.
- `backend/app/services/credential_registry.py` — **존속 필수**. connection validator가 이 dict를 SOT로 참조한다(위 단순화 제안 #1 참고).
- `backend/app/services/credential_service.py:45-54`의 함수 내부 import — 백로그 C 보고서에서 "M3에서 젠슨이 상단 이동" 권고 상태였으나 이번 E-M1 스코프도 아님. 건드리지 않음.
- 프론트엔드 `/connections` 페이지, `tool-configs-dialog` 등 — M5 UI 통합 범위.

---

## 결론

| 분류 | 건수 | 비고 |
|------|------|------|
| **즉시 삭제** | **0건** | M1은 신규 추가 전용 |
| **단순화 제안** | **5건** | 모두 S2/S3/S4 신규 파일 작성 가이드 — 기존 코드 변경 없음 |
| **보류 (후속 마일스톤)** | **8건** | M2 2건(MCPServer, Tool.mcp_server_id), M4 1건(Tool.credential_id), M5 1건(프론트 다이얼로그), M6 4건(Tool.auth_type/auth_config, AgentToolLink.config, resolve_server_auth, get_usage_count 재설계) |

**베조스 판단**: M1은 "추가만" 마일스톤이며, 기존 legacy 자산은 ADR-008 §이행 전략에 이미 제거 일정이 문서화되어 있다. 이 PR에서 legacy를 건드리면 parallel run 원칙이 깨져 롤백 가능성이 소실된다. **Drive-by 금지, Minimal Impact 원칙 준수.**

단순화 5건은 모두 **신규 파일 작성 가이드**로, 피차이(S2)와 젠슨(S3), 그리고 나 자신의 S4 구현이 drift/중복/보안 이슈를 피하도록 하는 사전 정렬 장치다.
