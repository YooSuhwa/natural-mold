# 삭제 분석 보고서 — 백로그 C (credentials list N+1 복호화 제거)

**브랜치**: `feature/credentials-field-keys-cache`
**작성자**: 베조스 (QA)
**작성일**: 2026-04-17
**스코프**: `backend/app/{services/credential_service.py, models/credential.py, routers/credentials.py, schemas/credential.py}`

---

## 즉시 삭제 가능

1. **`credential_service.create_credential`의 함수 내부 import (line 45, 48)**
   - 현재: `from app.config import settings` + `from app.exceptions import AppError`가 함수 본문 내부
   - 이유: 이 모듈은 이미 다른 서비스 모듈과 동일 패턴으로 상단에 `settings`/`AppError`를 올릴 수 있음. 순환 import 위험 없음 (다른 서비스/라우터들이 상단 import 사용 중). 지연 import 유지 이유가 문서화되지도 않았음.
   - 조치: M3 작업 시 젠슨이 상단으로 이동.

2. **`CredentialUpdate` 스키마 주석 없는 `data: dict[str, str] | None` 구조는 유지**
   - 삭제 아님, 다만 M3에서 `data.data is not None` 체크가 여전히 sole 트리거. `is_active` 갱신 경로는 현재 API에 존재하지 않으므로 (별도 토글 라우트 없음) `is_active` 관련 별도 분기 추가 금지.

---

## 삭제 검토 필요 (사티아 확인 필요)

1. **`credential_service.extract_field_keys`의 try/except 폴백 로직 유지 여부 (line 100-103)**
   - 현재: `try: list(resolve_credential_data(credential).keys()); except Exception: return []`
   - 캐시 컬럼 도입 후: 캐시 히트 100% 가정 시 try/except가 dead path로 전락. 하지만 ADR-007이 "legacy row fallback" 명시 + 백필 실패 허용(tolerant) 요구 → **존치 필수**.
   - 리스크: fallback을 삭제하면 ENCRYPTION_KEY 미설정 마이그레이션 환경 + 기존 row 조합에서 500 에러 가능.
   - 권고: **삭제하지 말 것.** M3 구현에서 `credential.field_keys is not None` 분기 우선 + 기존 try/except 경로는 원형 유지.

2. **`credentials.is_active` 컬럼 (models/credential.py:26) + 응답 스키마의 `is_active` 필드 (schemas/credential.py:26, routers/credentials.py:30)**
   - 현재: 생성 시 기본 True, 수정/토글 API 없음. 쿼리 필터에도 사용 안 됨 (`list_credentials`는 `is_active` 필터 없음).
   - **상태: 실질적 dead column** — 항상 True 반환. 응답/UI에 노출만 됨.
   - 리스크: 이번 백로그 C 스코프 밖. 비활성화 기능은 별도 피처로 설계해야 할 수 있음(소프트 삭제 정책).
   - 권고: **이번 PR에서는 건드리지 말 것.** 백로그 D/E에서 별도 항목으로 처리. 이번 스코프 외 변경 = drive-by 리팩토링.

3. **`CredentialResponse.has_data` 필드 (schemas/credential.py:27, routers/credentials.py:31)**
   - 현재: `bool(cred.data_encrypted)` — credential 생성 후 항상 True (create_credential이 `data_encrypted` 없이 row를 만들 경로 없음). 구조적으로 항상 True인 field.
   - 리스크: 클라이언트가 이 필드를 `hasOwnProperty`처럼 쓰고 있을 수 있음. 프론트엔드 전수 조사 필요.
   - 권고: **이번 PR에서는 유지.** 제거는 프론트엔드 확인 후 별도 PR에서.

---

## 단순화 제안

1. **`extract_field_keys` 캐시 + fallback 분기 형태**
   - 현재(예정): `if credential.field_keys is not None: return credential.field_keys` + try/except fallback
   - 제안: M3 구현 시 **early return 패턴** 유지 (plan 제안과 동일). 2 경로지만 가독성 우선 — 중첩 try/except 금지.
   - 근거: 두 경로는 의미가 다름(캐시 히트 vs legacy 복호화) → 병합하지 말 것. ADR-007의 "2 경로 유지 — 유지보수 부담 낮음" 결정을 존중.

2. **`create_credential`의 ENCRYPTION_KEY 가드 + AppError**
   - 현재: 503 반환 경로가 `create_credential` 내부에만 존재.
   - 제안: **변경 금지.** M2 마이그레이션은 "ENCRYPTION_KEY 미설정 시 skip + 경고 로그" (CHECKPOINT M2 명시) — create와 마이그레이션이 서로 다른 층위 정책. 일치시키려는 유혹을 거부.

3. **테스트 파일 위치**
   - 현재: credential 관련 커버리지가 `test_tools.py`(line 513 등)에 분산.
   - 제안: M4에서 `test_credentials.py` 신설 시 credential-only 시나리오는 모두 신규 파일로. 기존 `test_tools.py`의 `resolve_credential_data` patch 패턴(513-534)은 **중복 복제하지 말고** 참고만 — 기존 테스트 건드리지 말 것 (회귀 면).

4. **`_to_response`의 `extract_field_keys(cred)` 호출 경로**
   - 현재: 순회마다 호출 → 캐시 도입 후 순회마다 속성 읽기.
   - 제안: **추가 단순화 불필요.** `_to_response`는 pure mapper로 유지. list comprehension 순회는 Python 호출 비용이 Fernet 대비 무시 가능 수준이므로 추가 벡터화 금지.

---

## 베조스 의견 (결론)

- **이번 PR에서 실제 "삭제" 할 항목: 0개**
- **단순화 조정: 1개** (create_credential 함수 내부 import → 상단 이동, M3에서 젠슨 처리)
- **보류: 3개** (is_active, has_data, fallback try/except) — 스코프 외 또는 의도된 보존 대상

"Good enough는 없다"지만 **범위 이탈도 없다.** 백로그 C는 N+1 복호화 제거가 목표 — credential 모델/스키마의 다른 문제점은 각자의 티켓으로 분리해야 한다. Minimal Impact 원칙.
