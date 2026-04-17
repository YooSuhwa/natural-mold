# ADR-007: Credentials `field_keys` 비암호화 캐시 컬럼

## 상태: 승인됨

## 날짜: 2026-04-17

## 맥락

`GET /api/credentials`는 각 credential에 대해 `data_encrypted`를 Fernet으로 복호화하고 JSON 파싱하여 `field_keys` 목록(키 이름만)을 응답에 포함한다. 현재 `credential_service.extract_field_keys()` → `resolve_credential_data()` → `decrypt_api_key()` 경로가 list 결과를 순회하며 호출되어 **N+1 복호화**가 발생한다. DB 쿼리는 1회지만 Fernet 연산이 row 수만큼 반복된다.

- 100 credential 기준 응답 추정 지연: ~1–2초 (CPU 바운드)
- 복호화 비용이 credential 증가에 선형 비례

## 결정

`credentials` 테이블에 `field_keys` 컬럼을 추가한다:

- 타입: `sa.JSON()` (PostgreSQL JSONB 매핑, SQLite aiosqlite 호환)
- nullable=True (legacy row backfill 전 대응)
- 저장 내용: **키 이름 목록만** (예: `["api_key"]`, `["client_id", "client_secret"]`) — 값은 여전히 `data_encrypted`에 Fernet으로 유지

`create_credential` / `update_credential`에서 data 변경 시 캐시를 동기화한다. `extract_field_keys()`는 캐시 우선, NULL이면 기존 복호화 경로로 fallback한다.

Alembic 마이그레이션(`m7_add_credential_field_keys`)의 `upgrade()`에서 기존 row에 대해 일회성 backfill을 수행한다(ENCRYPTION_KEY 미설정 시 스킵).

## 대안

- **A. Runtime lazy write-through**: 컬럼만 추가하고 read 경로에서 NULL → 복호화 → 저장. 단순하지만 read에 write 부작용 발생, 동시성 주의 필요.
- **B. Runtime lazy fallback만**: 마이그레이션에서 backfill 없이 NULL로 두고 생성/갱신 시점에만 캐시. 기존 row는 영영 fallback 경로로만 서빙됨 (성능 개선 불완전).
- **C. 별도 테이블 `credential_meta`**: 메타데이터 분리. 과도한 구조화 — 이 케이스에는 불필요.
- **D. 응답 스키마에서 `field_keys` 제거**: 클라이언트 호환성 깨짐. UI가 이 목록으로 form UI를 구성 중이므로 기각.

## 결과

### 긍정

- List API 응답 시 복호화 0회 (캐시 히트 시)
- 응답 스키마 불변 → 클라이언트 변경 불필요
- Legacy row는 fallback 경로로 점진적 마이그레이션 허용 (A와 동등한 안전망)

### 부정

- `credentials` 테이블에 컬럼 1개 추가 (미미)
- 2 경로 유지 (캐시/fallback) — 단, fallback은 동일 로직 재사용이라 유지보수 부담 낮음

### 보안

- `field_keys`는 **키 이름만** 저장. 값이 아님.
- 기존에도 API 응답에 키 이름이 노출되었으므로 기밀성 악화 없음.
- `data_encrypted`는 그대로 Fernet 암호화 유지.

## 관련 문서

- 플랜: `~/.claude/plans/c-credentials-list-glistening-kurzweil.md`
- 실행 계획: `docs/exec-plans/active/backlog-c-field-keys-cache.md`
- 이전 ADR: ADR-005(Builder/Assistant), ADR-003(스킬+메모리)
