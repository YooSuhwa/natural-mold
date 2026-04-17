# CHECKPOINT — credentials list N+1 복호화 제거 (백로그 C)

**브랜치**: `feature/credentials-field-keys-cache`
**플랜**: `~/.claude/plans/c-credentials-list-glistening-kurzweil.md`
**시작**: 2026-04-17
**worktree**: `/Users/chester/dev/natural-mold/.claude/worktrees/backlog-c`

## M0: Docs/ADR 초기화

- [x] `docs/design-docs/adr-007-credentials-field-keys-cache.md` 작성
- [x] `docs/exec-plans/active/backlog-c-field-keys-cache.md`에 실행 계획 사본
- 검증: 두 파일 존재 + `docs/design-docs/index.md`에 신규 ADR 링크 (완료)
- done-when: ADR 번호 부여, 맥락/결정/대안/결과 4 섹션 작성
- 상태: **done**
- 담당: 사티아 (피차이 대리 — 경량팀 구성)

## M1: 삭제 분석

- [x] 삭제 0건, 단순화 1건, 보류 3건 — scope 외 drive-by 금지 원칙 준수
- [x] `tasks/deletion-analysis-c.md` 보고서 작성
- 검증: 보고서 존재 + 권고 사항 명시 (완료)
- done-when: 사티아가 보고서 승인
- 상태: **done**
- 담당: 베조스

## M2: 모델 + Alembic 마이그레이션 + 백필

- [ ] `backend/app/models/credential.py`에 `field_keys: Mapped[list[str] | None]` (sa.JSON(), nullable=True) 추가
- [ ] 신규 마이그레이션 `backend/alembic/versions/m7_add_credential_field_keys.py` — upgrade: 컬럼 추가 + 기존 row backfill, downgrade: drop_column
- [ ] ENCRYPTION_KEY 미설정 시 backfill 스킵 + 경고 로그
- 검증: `cd backend && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` — 왕복 PASS
- done-when: 왕복 성공, 테이블에 field_keys 컬럼 존재
- 상태: **done**
- 담당: 젠슨

## M3: 서비스 수정 (create/update/extract)

- [ ] `credential_service.create_credential` — Credential 생성 시 `field_keys=list(data.data.keys())`
- [ ] `credential_service.update_credential` — `data.data` 변경 시 `field_keys` 동기화. name만 변경 시 불변
- [ ] `credential_service.extract_field_keys` — 캐시 우선, NULL이면 기존 복호화 경로 fallback
- 검증: `cd backend && uv run ruff check app/services/credential_service.py app/models/credential.py` — PASS
- done-when: ruff PASS, import 순환 없음
- 상태: **done**
- 담당: 젠슨

## M4: 테스트 신설 + 회귀

- [ ] 신규 `backend/tests/test_credentials.py` — 5 시나리오:
  1. create 시 field_keys 저장
  2. update(data 변경) 시 field_keys 동기화
  3. update(name만) 시 field_keys 불변
  4. list 응답에서 decrypt_api_key 호출 0회 (monkeypatch/spy)
  5. legacy row(field_keys=None) fallback 경로 동작
- [ ] 전체 회귀 `uv run pytest` 540+ 유지
- 검증: `cd backend && uv run pytest tests/test_credentials.py -v && uv run pytest` — **545 passed**
- done-when: 신규 5 통과, 기존 회귀 0
- 상태: **done**
- 담당: 베조스

## M5: 통합 + 커밋

- [x] 전체 verify: `ruff check . && pytest && alembic upgrade/downgrade/upgrade` — 모두 PASS (545 passed, ruff clean, 왕복 성공)
- [x] `HANDOFF.md` 업데이트 (진행 중)
- [x] worktree 내 단일 커밋 (feat(credentials): field_keys cache column)
- 검증: `git log --oneline feature/credentials-field-keys-cache ^main`
- done-when: 커밋 존재, 전체 verify PASS
- 상태: **done**
- 담당: 사티아
