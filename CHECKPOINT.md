# CHECKPOINT — Marketplace Resources Phase 1

**Project Owner**: 사티아 (Satya)
**Branch**: `worktree-marketplace-resources`
**Source docs**: `docs/marketplace-resources-prd.md` v0.2, `docs/marketplace-resources-spec.md` v0.1
**Handoff order (Spec §19)**: A → B → D → E → C → F → G

---

## 핵심 결정사항 (PRD/Spec에서 확정)

| 항목 | 결정 | 출처 |
|------|------|------|
| 마켓플레이스 범위 | Agent / MCP / Skill (Tool 비목표) | PRD §3 |
| Phase 1 범위 | Skill marketplace + selected-skill mount + credential injection + k-skill importer | PRD §14 |
| Alembic 분할 | m40~m43 슬라이스별 마이그레이션 | Spec §3.1 |
| Agent-Skill override | `agent_skills.config` JSON | Spec §0.1 |
| Runtime mount | per-thread `copytree` 데이터 격리 | Spec §0.1, §9 |
| k-skill source | GitHub `NomaDamas/k-skill` clone | Spec §0.1 |
| Public publish | published vs listed 분리 (super_user `is_listed` 토글) | Spec §0.1 |
| Visibility | private/restricted/public/unlisted/system | PRD §7 |
| Credential | Cipher V2 재사용 + 신규 8개 definition (`srt_account` 등) | PRD §8 |

---

## M1: 사일로 셋업 (S0~S2)
- [ ] S0 (피차이): docs/design-docs/adr-017-marketplace-resources.md + ARCHITECTURE.md marketplace 섹션
- [ ] S1 (베조스): tasks/deletion-analysis.md — runtime broad mount/env 빈 구멍/packager secret scan 부재 식별
- [ ] S2 (피차이): 모듈 경계/ORM 파일 계약/Pydantic 스키마 contracts
- 검증: `test -f docs/design-docs/adr-017-marketplace-resources.md && test -f tasks/deletion-analysis.md && grep -q marketplace docs/ARCHITECTURE.md`
- done-when: 아키텍처 명문화 + 삭제 분석 + 모듈 경계
- 상태: pending

## M2: Slice A — 데이터 + Read Catalog
- [ ] 젠슨: m40_marketplace_tables.py (5개 + circular FK)
- [ ] 젠슨: m41_skills_marketplace_columns.py (12개 컬럼 + backfill)
- [ ] 젠슨: m42_agent_skills_config.py
- [ ] 젠슨: m43_skill_credential_bindings.py
- [ ] 젠슨: app/models/marketplace.py (ORM)
- [ ] 젠슨: app/marketplace/{access,schemas,origin_service,service}.py + routers/marketplace.py
- [ ] 젠슨: 기존 /api/skills 응답에 origin_summary + publication_summary 추가
- 검증: `cd backend && uv run ruff check . && uv run pytest tests/test_marketplace_*.py -v && uv run alembic upgrade head && uv run alembic downgrade -4 && uv run alembic upgrade head`
- done-when: 마이그레이션 reversible, 접근 매트릭스 통과, 기존 skill 회귀 통과
- 상태: pending

## M3: Slice D — Credential Definitions + Binding
- [ ] 젠슨: app/credentials/definitions/{srt_account,ktx_account,foresttrip_account,kipris_plus_api,dart_api,odsay_api,coupang_partners,k_skill_proxy}.py
- [ ] 젠슨: app/marketplace/credential_requirements.py (env injection plan)
- [ ] 젠슨: routers/skills.py에 credential-bindings 엔드포인트 추가
- 검증: `cd backend && uv run pytest tests/test_credential_definitions.py tests/test_skill_bindings.py -v`
- done-when: owner/definition_key 검증, needs_setup 응답 정확
- 상태: pending

## M4: Slice B — Install
- [ ] 젠슨: app/marketplace/install_service.py
- [ ] 젠슨: POST install + update + DELETE installation
- [ ] 젠슨: install_mode 처리
- 검증: `cd backend && uv run pytest tests/test_marketplace_install.py -v`
- done-when: 설치가 user-owned skills 생성, installation source 추적
- 상태: pending

## M5: Slice E — Runtime Mount + Credential Injection (보안 critical)
- [ ] 젠슨: executor.py:build_agent 패치 (per-thread copytree)
- [ ] 젠슨: _create_skill_execute_tool 시그니처 변경 + env injection
- [ ] 젠슨: app/marketplace/redaction.py + streaming.py/tool result/exception에 적용
- [ ] 젠슨: fail-fast missing credential
- [ ] 젠슨: stale runtime root cleanup
- 검증: `cd backend && uv run pytest tests/test_runtime_isolation.py tests/test_credential_injection.py tests/test_redaction.py -v`
- done-when: 미선택 skill 차단, mapped env만 노출, log/SSE redacted
- 상태: pending

## M6: Slice C — Publish + Secret Scan
- [ ] 젠슨: app/marketplace/secret_scan.py + publish_service.py
- [ ] 젠슨: POST from-skill / versions/from-skill / ACL / disable 라우터
- [ ] 젠슨: routers/skills.py:upload에 secret_scan 적용 (회귀 가드)
- 검증: `cd backend && uv run pytest tests/test_publish.py tests/test_secret_scan.py -v`
- done-when: secret 거부, immutable version, ACL 강제
- 상태: pending

## M7: Slice F — k-skill Importer
- [ ] 젠슨: app/marketplace/k_skill_importer.py + k_skill_requirements.py (curated map)
- [ ] 젠슨: app/scripts/sync_k_skill.py CLI
- [ ] 젠슨: app/config.py에 k_skill_* 4개 settings
- 검증: dry-run 실행 + `uv run pytest tests/test_k_skill_importer.py -v`
- done-when: idempotent sync, 단일 실패가 전체 중단 안 함
- 상태: pending

## M8: Slice G — Frontend Marketplace UI
- [ ] 팀쿡: docs/design-docs/marketplace-ui-spec.md
- [ ] 저커버그: /marketplace 페이지 + install/publish wizard
- [ ] 저커버그: lib/api/marketplace.ts + hooks
- [ ] 저커버그: /skills, /mcp-servers에 origin/publication badge
- 검증: `cd frontend && pnpm build && pnpm lint`
- done-when: 빌드 통과 + 카드 CTA 동작
- 상태: pending

## M9: 통합 검증 + HANDOFF
- [ ] 베조스: E2E (PRD §10.1~10.7) + permission matrix + runtime isolation + secret safety
- [ ] 베조스: tasks/lessons.md + docs/QUALITY_SCORE.md
- [ ] 사티아: HANDOFF.md + docs/ARCHITECTURE.md 정리
- 검증: `cd backend && uv run pytest -v && cd ../frontend && pnpm build && pnpm lint`
- done-when: Phase 1 출시 게이트(PRD §13) 8개 PASS
- 상태: pending

---

## 마일스톤 의존 그래프

```
M1 ─ M2 ─┬─ M3 ─┐
         └─ M4 ─┴── M5 ── M6 ── M7 ── M8 ── M9
```

**병렬 가능**: M3/M4는 M2 완료 후 병렬. M8(frontend)은 M5/M6 완료 후 backend stable 상태에서.
**Critical Path**: M1 → M2 → M4 → M5 → M9

---

## 컨텍스트 가드

- 슬라이스 시작 전 progress.txt 반드시 읽기
- verify 통과 없이 완료 마킹 금지 (Ralph: 검증 없이 완료 없음)
- 3회 실패 → 사티아에게 ESCALATION
- 컨텍스트 오염 신호 → progress.txt 덤프 → 리스폰
