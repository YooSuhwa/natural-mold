# ADR-009: Credential / Tools / Skills 그린필드 리라이트

**상태**: Accepted
**날짜**: 2026-04-29
**결정자**: 사티아 (PO), 피차이 (아키텍처 DRI)
**관련**: ADR-007 (credentials field_keys), ADR-008 (Connection entity)

---

## Context

`Credential` / `Connection` / `Tools` / `Skills` 시스템은 ADR-007 ~ ADR-008과 M6 ~ M11 마이그레이션으로 누적 진화해왔다. 결과:

1. **이원화**: Credential과 Connection이 분리되어 N:1 관계, 도구 인증 경로가 PREBUILT/CUSTOM/MCP/BUILTIN 4분류로 분기됨.
2. **인증 해석 복잡도**: `chat_service.build_tools_config()` (L369-462)에 `_resolve_prebuilt_auth`, `_resolve_custom_auth`, `_gate_connection_active`, `_gate_connection_credential` 등 분기가 누적.
3. **마이그레이션 누적**: `m11_custom_credential_migration`이 `tool.credential_id` → `tool.connection_id`로 옮겼지만, 기존 컬럼 잔존, downgrade 복잡.
4. **OAuth/Vault/키 로테이션 부재**: 자동 OAuth refresh, 외부 시크릿 저장소(Vault), 다중 키 로테이션 모두 미지원.
5. **UI 일관성 부족**: 도구·연결·자격증명·스킬 페이지 각각 다른 카드 그리드, 상태 표시 통일 안 됨.

이 상태에서 OAuth refresh, External Secrets, 멀티키 로테이션, 동적 폼 검증 등을 추가하면 또 한 번의 M11급 마이그레이션이 필요하다.

## Decision

n8n의 검증된 추상화(`Cipher V2`, `ICredentialType`, `GenericAuth`, `OAuth2 helper`, `External Secrets`, `NodeParameters` 동적 폼)를 **알고리즘만 차용**하여 Python(FastAPI/SQLAlchemy)·React(Next.js/shadcn)로 그린필드 리라이트한다. **결과물에 "n8n" 식별자·로고·라이선스 헤더는 일절 남기지 않는다.**

### 핵심 결정

| # | 결정 | 근거 |
|---|---|---|
| 1 | **이원화 폐기** | Credential 단일화. Connection 모델·라우터·서비스 모두 삭제. Tool은 `definition_key + parameters + credential_id FK`로 단일 경로. |
| 2 | **Cipher V2** | n8n core 알고리즘(HKDF-SHA256, AES-256-GCM, 단일 블롭 Base64 `[0x01][salt 32B][authTag 16B][ciphertext]`) 차용. HKDF info는 `b'moldy-encryption-v1'`로 자체화. 멀티키 식별은 `credentials.key_id` 별도 컬럼. |
| 3 | **LLM 모델 통합** | `models` 테이블 유지하되 `api_key_encrypted` 컬럼 제거. `agents.llm_credential_id` FK 추가. `llm_providers` 테이블 폐기. LLM API 키도 신규 Credential로. |
| 4 | **OAuth2 자동 refresh** | n8n preAuthentication 패턴 차용. `expirable` typeOptions 토큰 만료 검사 → refresh → 재암호화 → audit log. 동시성: `SELECT ... FOR UPDATE`로 직렬화. |
| 5 | **External Secrets (Vault)** | HVAC SDK 실구현. feature flag(`settings.external_secrets_enabled`)로 기본 off. `__external__: { provider, ref }` 마커 런타임 해석. |
| 6 | **자동 키 로테이션** | APScheduler 잡 `rotate_credentials_to_active_key` 주1회. `key_id != active_key_id`인 row 배치 재암호화. audit log `rotate`. |
| 7 | **마이그레이션** | `m13_greenfield_credentials` 단일 마이그레이션. 모든 관련 테이블 DROP + CREATE + `agents.llm_credential_id` ADD. PoC라 dev DB 폐기 OK. downgrade는 `NotImplementedError`. |
| 8 | **단일 PR** | 신규 파일 위주 ~80파일. dual 시스템 공존 회피. 마일스톤별 커밋으로 리뷰 가독성 확보. |
| 9 | **브랜딩 검증** | `scripts/check_branding.py`가 CI 게이트. `\bn8n\b` 0건 + `@n8n/*` 패키지 0건 + 로고 SHA-256 블랙리스트. 화이트리스트는 `NOTICES.md`만. |

### 의도적으로 차용하지 않는 것

- n8n 노드 시스템 전체(과대 적합) — 도구는 `ToolDefinition` 단일 타입으로 단순화
- n8n 표현식 엔진 전체(보안 표면 과대) — `={{ $credentials.<field> }}`만 평가하는 한정 인터폴레이터
- n8n EE 모듈 전체(라이선스/스코프 외)
- n8n Vue 컴포넌트(언어 불일치) — UI 패턴(정보 구조, 인터랙션)만 차용해 React+shadcn으로 새로 작성

## Consequences

### Positive

- 인증 해석 경로 단일화: `tool.credential_id` 직결, 분기 폐기
- OAuth/Vault/로테이션 등 운영 기능 1차 도입
- Cipher V2로 키 라이프사이클 관리 가능
- UI 일관성: 동일한 DataTable + 상태 칩 + 동적 폼 렌더러
- 신규 도구·자격증명 추가 비용 감소(정의만 등록)

### Negative

- dev DB 데이터 폐기 (PoC 단계라 수용)
- 단일 PR 리뷰 부담 (마일스톤 커밋으로 완화)
- 라이선스 검토 1회 필요 (n8n SUL — 차용 알고리즘 영역은 Apache-2.0)
- 학습 곡선: 팀이 신규 도메인 모델 숙지 필요

### Risks & Mitigations

| 리스크 | 대응 |
|---|---|
| n8n 식별자 누락 | `scripts/check_branding.py` CI 게이트 강제 |
| OAuth refresh 동시성 | `oauth2_base`에서 `SELECT ... FOR UPDATE` 직렬화 |
| 채팅/트리거 회귀 | M5에서 chat_service 재작성 후 즉시 채팅+도구+트리거+MCP 시나리오 회귀 테스트 |
| Vault 의존성 | feature flag 기본 off, env_provider로 폴백 |
| 라이선스 적합성 | NOTICES.md 출처 명기, 머지 전 변호사 1회 검토 (PoC + 사내라 SUL "경쟁 SaaS" 비해당) |

## Implementation

마일스톤 정의는 `CHECKPOINT.md`, 상세 파일/스펙은 루트 `PLAN.md`, 작업 추적은 TaskList(`tth-greenfield-credentials` 팀).

작업 순서:
- M0 거버넌스(이 ADR 포함) → M1 브랜딩 검증 + Cipher V2 → M2 Credential + Vault → M3 Tools + MCP → M4 Skills + m13 마이그레이션 → M5 agent_runtime 재배선 + cron → M6 프론트엔드.

## References

- 탐색 보고서: `progress.txt` "n8n 차용 핵심" 섹션
- n8n 소스: `/Users/chester/dev/ref/n8n` (Apache-2.0 + SUL 듀얼)
- 라이선스 메모: `NOTICES.md` (M1 산출물)
- 이전 ADR: ADR-007(field_keys), ADR-008(Connection — 본 ADR로 폐기 승계)
