# Stage 2 — 레이어링·경계 ✅ 완료 (PR #295)

브랜치: `refactor/stage2-layering` (origin/main 기준), 한 PR = Stage 2 전체(항목 7–10), 항목별 커밋 + 항목별 리뷰.

- [x] BE-S7: credentials 라우터 OAuth → oauth_service (899bc1be)
  - [x] 리뷰: 승인(Critical/High 0, Low 2) → [x] 수정: 교차 사용자 forbidden 회귀 테스트 (9dbf3ece)
  - PR 본문 명시됨: start 에러 경로 commit→rollback 발산(정책상 의도)
- [x] BE-S2: MCP/tools/models 서비스 레이어 신설 (0a0e4c81, 07bc3ba6, 939b876b)
  - [x] 리뷰: 승인(발견 0, Low 2건 정보성 — 조치 불필요)
- [x] BE-D3: audit record_self_event 래퍼 + 18곳 치환 (50fdb7c1)
  - [x] 리뷰: Medium 2 → [x] 수정: finalize 누락 사이트 + 신원 컬럼 계약 테스트 (ba3e1493)
- [x] BE-D7: 테스트 팩토리 + auth 세션 헬퍼 (5ac614b2)
  - [x] 리뷰: 승인(Critical/High/Medium 0, Low 2건 정보성)
- [x] 전체 검증: ruff 그린 / pytest 2668 passed (SKILL_EVALUATION_ENABLED=true) / integration 직렬 29 passed
- [x] 푸시 → PR #295 → 플랜 문서 ✅ 갱신

다음(Stage 3): BE-S1 chat_service 8-클러스터 분해 → BE-S3 install_service 분해.
