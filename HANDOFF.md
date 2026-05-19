# HANDOFF — Marketplace Resources Phase 1

**세션**: 2026-05-18 ~ 2026-05-19
**브랜치**: `worktree-marketplace-resources` (worktree at `.claude/worktrees/marketplace-resources`)
**소스 PRD/Spec**: `docs/marketplace-resources-prd.md` v0.2, `docs/marketplace-resources-spec.md` v0.1
**팀**: 사티아(PO) + 피차이(아키텍처) + 젠슨(백엔드) + 팀쿡(디자인) + 저커버그(프론트엔드) + 베조스(QA)

---

## 결과

**Phase 1 출시 게이트 8개 모두 PASS → Full GO**

| Gate | 통과 |
|------|------|
| Access control (private/restricted/public/system + 404 enumeration oracle 통일) | ✅ |
| Secret safety (secret_scan + redaction multi-channel) | ✅ |
| Runtime isolation (per-thread root + selected-skill mount) | ✅ |
| Credential runtime (fail-fast + mapped env only + override priority) | ✅ |
| k-skill sync (idempotent + single-failure isolation + dry-run) | ✅ |
| Backward compatibility (skill upload/edit/delete + AgentSkillLink + /api/skills 회귀 0) | ✅ |
| Listing 승인 (public unlisted-by-default + super_user 토글) | ✅ |
| ADR-016 정합 (모든 mutation route verify_csrf + admin require_super_user) | ✅ |

검증:
- **Backend pytest 1191 passed, 0 xfailed, 0 회귀** (이전 baseline 972 → +219 신규 테스트)
- ruff `check .` 0 error
- Alembic `upgrade head && downgrade -4 && upgrade head` reversible
- Frontend `pnpm lint` 0 error, `pnpm build` PASS (3 marketplace 라우트 생성)

---

## 변경 사항 요약

### 백엔드 (Slice A~F)
| 영역 | 신규 파일 | 합계 LOC |
|------|----------|----------|
| ORM | `models/marketplace.py` (6 클래스), `models/skill.py` (확장) | 600+ |
| 마이그레이션 | `alembic/versions/m40~m43_*.py` (4개, reversible) | 약 800 |
| Service 모듈 | `marketplace/{service,install_service,publish_service,credential_requirements,origin_service,secret_scan,redaction,skill_runtime,k_skill_importer,k_skill_requirements}.py` (10 모듈) | 4,727 |
| 라우터 | `routers/marketplace.py` (13 endpoint), `routers/skills.py` 확장 (binding 4 endpoint + secret_scan integration) | — |
| 신규 credential definitions | `srt_account, ktx_account, foresttrip_account, kipris_plus_api, dart_api, odsay_api, coupang_partners, k_skill_proxy` (8개, 13→21) | — |
| CLI | `scripts/sync_k_skill.py` (--ref/--dry-run/--only/--keep-deprecated/--skip-git) | 234 |
| Agent runtime | `agent_runtime/executor.py` per-thread mount + credential env injection 패치, `scheduler.py` retention job | — |
| 에러 코드 | 10종 추가 (`marketplace_item_not_found`, `marketplace_credential_required`, `marketplace_secret_detected` 등) | — |

### 프론트엔드 (Slice G)
| 영역 | 신규 |
|------|------|
| 페이지 | `/marketplace`, `/marketplace/[item-id]`, `/marketplace/admin/moderation` |
| 컴포넌트 | `marketplace-card.tsx`, `marketplace-filter-bar.tsx`, `install-wizard.tsx`, `publish-wizard.tsx`, `badges/` |
| API/Hooks/Types | `lib/api/marketplace.ts`, `lib/hooks/use-marketplace.ts`, `lib/types/marketplace.ts` |
| 보강 | `/skills` 페이지 origin/publication/credential badge, 사이드바 Marketplace 메뉴 |

### 테스트 (베조스 주도, 일부 젠슨)
| 파일 | 카운트 |
|------|--------|
| `test_skills_api_regression.py` | 15 (1 xfail XPASS로 promote됨) |
| `test_marketplace_migration.py` | 11 |
| `test_marketplace_access.py` | 25 |
| `test_marketplace_listing.py` | 12 (M2.5 정정 후 7→12) |
| `test_credential_definitions.py` | 6 |
| `test_skill_bindings.py` | 9 |
| `test_marketplace_install.py` | 6 |
| `test_runtime_isolation.py` | 10 |
| `test_credential_injection.py` | 10 |
| `test_redaction.py` | 16 |
| `test_secret_scan.py` | 53 |
| `test_marketplace_publish.py` | 8 (jensen 작성) |
| `test_k_skill_importer.py` | 13 |
| `test_marketplace_e2e.py` | 7 (PRD §10.1~10.7 시나리오) |
| `test_marketplace_phase1_gates.py` | 22 (8 게이트 매트릭스) |
| **합계** | **약 220 신규 assertion** |

---

## 아키텍처 결정 (ADR-017)

1. **마켓플레이스 범위 = Agent / MCP / Skill** — Tool은 비목표 (`tools/registry.py` 운영자 코드 정의)
2. **데이터 모델**: marketplace_items + versions + installations + acl + publication_links + skill_credential_bindings (6 신규 + skills/agent_skills 확장)
3. **Skill runtime mount**: per-thread `data/runtime/{thread_id}/skills/{slug}/` copytree (symlinks=False). 매 turn mtime 갱신으로 active 보호, 1시간 retention.
4. **Credential override priority**: `agent_skills.config.credential_bindings.{key}` > `SkillCredentialBinding.scope='skill'` > missing → fail-fast `marketplace_credential_required` 409
5. **Redaction multi-channel**: subprocess stdout/stderr + SSE TOOL_CALL_START.parameters + exception traceback 자동 redact (`<redacted:<env_name>>` 마커, 5자 미만 skip, 길이 정렬)
6. **Publish vs Listed 분리**: 누구나 publish 가능 (`is_listed=False` 시작), `is_listed=True` 토글은 super_user 전용 admin endpoint
7. **k-skill importer**: super_user CLI 전용 (`uv run python -m app.scripts.sync_k_skill --ref main`), curated `K_SKILL_REQUIREMENT_MAP` source of truth, content_hash dedup으로 idempotent

ADR-017: `docs/design-docs/adr-017-marketplace-resources.md` (236L)
Module contracts: `docs/design-docs/marketplace-module-contracts.md` (561L)
UI spec: `docs/design-docs/marketplace-ui-spec.md` (839L)

---

## Course Corrections + Open Items 처리

**strict xfail pin + ?-프로토콜**로 spec 위반 4건을 모두 자동 감지·해소.

| ID | 발견자 | 항목 | 처리 |
|----|--------|------|------|
| OI-1 | 베조스 M1-S1 | credential definitions 13 vs 14 (PRD/progress.txt 오기) | 13개로 확정, PRD/Spec/ADR/progress.txt 4곳 정정 |
| OI-2/3 | 베조스 M1-S1 | Slice E의 `SkillToolContext` dataclass + helper 권장 | 젠슨 Slice E 단계 1에서 적용 |
| OI-4 | 베조스 M1-S1 | secret_scan `sk-` 패턴 word boundary 필요 | Spec §13.1을 `\bsk-[A-Za-z0-9_-]{20,}\b`로 정정 + Slice C 적용 |
| OI-5 | 베조스 M1-S1 (Task #15) | `create_package_skill`이 origin_kind 미설정 | strict xfail pin → 젠슨 1줄 fix → XPASS → 베조스 promote |
| M2.5 | 베조스 M2 | catalog list 기본이 unlisted public 포함 (Spec §10.1 위반) | service.py `_build_visible_items_query` 정정 + 베조스 listing test 재작성 (7→12 tests) |
| OPEN-1 | 베조스 M9 | `install_service.install_item` acl_entries lazy load → 500 (enumeration oracle 위반) | strict xfail pin → 젠슨 4줄 `selectinload` fix → XPASS → 베조스 promote, **Full GO** |
| executor test 회귀 | 베조스 Stage 2 | `test_executor.py:73/426`의 `skills == ["/skills/"]` deprecated | 사티아 컨펌 → 젠슨이 1줄 정정 (영역 침범 OK) |

**핵심 패턴**: 베조스가 "분명히 미구현된 spec 항목"을 strict xfail로 pin → 다른 팀원 fix 시 자동 XPASS → strict 위반 → 베조스가 promote. OI-5와 OPEN-1이 정확히 이 흐름으로 두 번 작동.

---

## 삭제된 항목 (Musk Step 2)

베조스 deletion analysis (`tasks/deletion-analysis.md`, 354L) 결과:
- **즉시 삭제 가능 코드 0건**. 마켓플레이스는 "메우는 작업"이고, `executor.py`의 `legacy` 주석 6곳은 모두 historical context로 유지.
- 다만 `executor.py:_create_skill_execute_tool` 시그니처 확장 시 인자 폭증 대비 `SkillToolContext` dataclass + helper 추출(OI-2/3) — 단순화 권장이 채택됨.

---

## Ralph Loop 통계

| 마일스톤 | 1회 통과 | 재시도 후 통과 | Course correction | 에스컬레이션 |
|----------|---------|----------------|-------------------|--------------|
| M1 (셋업) | 3 | 0 | 0 | 0 |
| M2 (Slice A) | 1 | 0 | 1 (M2.5) | 0 |
| M3 (Slice D) | 1 | 0 | 0 | 0 |
| M4 (Slice B) | 1 | 0 | 0 | 0 |
| M5 (Slice E, 보안 critical) | 1 (4 stages) | 0 | 0 (test_executor.py:73/426 fix 1줄) | 0 |
| M6 (Slice C) | 1 (+ Task #15 묶음) | 0 | 0 | 0 |
| M7 (Slice F) | 1 | 0 | 0 | 0 |
| M8a/b (Slice G) | 2 | 0 | 0 | 0 |
| M9 (통합) | 1 | 0 | 1 (OPEN-1) | 0 |

총: 모든 마일스톤이 1회 또는 단계별 통과. 3회 실패 ESCALATION 0건.

---

## 컨텍스트 메모리 (다음 세션을 위해)

### 파일 위치
- 마이그레이션 최신: m43_skill_credential_bindings (다음 신규는 m44)
- Skill 모델 컬럼: 12 신규 (origin_kind, source_marketplace_*, is_dirty 등) — Spec §3.7
- AgentSkillLink.config JSON — credential_bindings override 저장
- ORM relationship 순환 FK: `MarketplaceItem.latest_version_id ↔ MarketplaceVersion` → `post_update=True` + Alembic 3단 ALTER

### Runtime 동작
- Skill 실행 시 `_DATA_DIR/runtime/{thread_id}/skills/{slug}/`에 매 turn copytree (symlinks=False)
- subprocess env에 mapped credential env var 자동 주입, log/SSE/tool result는 자동 redact
- 미선택 slug 호출 시 `"Error: skill not attached to this agent: <slug>"` 반환
- 1시간 retention scheduler job: `app.scheduler.cleanup_skill_runtime_roots`

### Spec/PRD 정정 사항 (이 세션에서 반영됨)
- PRD §6/§9, Spec §1.3, ADR-017 line 42/45: credential definitions "14개" → "13개" 정정
- Spec §13.1 SECRET_CONTENT_PATTERNS: `sk-[A-Za-z0-9]{16,}` → `\bsk-[A-Za-z0-9]{20,}\b` (OI-4 boundary)

---

## 남은 작업 (Phase 2 이후)

- [ ] **M8b polish**: Install/Publish Wizard의 edge case (네트워크 오류, 인증 만료, dirty 상태 update 시 strategy 선택) — 골격만 있음
- [ ] **MCP marketplace (Phase 2)**: `mcp_servers` publish/install + env_vars/headers credential requirement 변환
- [ ] **Agent marketplace (Phase 3)**: agent spec + tool/MCP/skill dependency graph + bundle install
- [ ] **k-skill upstream 운영 검증**: 실제 `NomaDamas/k-skill` sync 한 번 실행해 first-wave skill (`korean-spell-check` 등) 카탈로그 등록 확인
- [ ] **K_SKILL_REQUIREMENT_MAP 확장**: 현재 8개 매핑, k-skill 80개 전체 매핑은 별도 큐레이션 작업
- [ ] **Frontend polish**: 풀 wizard edge case, 모바일/태블릿 적응형 (FilterBar Sheet, Wizard LineTabs), shadcn/ui Dialog → DialogShell 통일
- [ ] **Phase 4 governance**: moderation status, deprecate/disable versions, usage analytics

---

## 배운 점 (이번 세션 핵심 패턴 — `tasks/lessons.md`에서 발췌)

1. **strict xfail pin**: 미구현 spec을 strict xfail로 pin → 구현 완료 시 자동 XPASS → strict 위반 → promote. backpressure를 자동화.
2. **?-프로토콜 cross-team**: 파일 경계 침범이 명백한 fix를 발견 시 ? 보고 → 사티아 컨펌 → 1줄 fix OK 흐름. 컨펌 비용 < 잘못된 invariant 비용.
3. **Course correction Task pattern**: spec 위반 발견 시 별도 Task로 등록 → 의존 chain에 끼워넣기 (M2.5 → M3 끝난 뒤). 직접 진행 대신 형식화.
4. **Multi-channel redaction**: log + SSE + tool result + exception 4 채널 통합 helper로 정책 일관성.
5. **per-thread runtime root**: LangGraph thread_id를 격리 키로 재사용. cleanup은 mtime 기반 (active 대화는 매 turn 갱신으로 자동 보호).
6. **enumeration oracle envelope equality**: 404 status code뿐 아니라 response body shape도 동등해야 → `r_hidden.json() == r_missing.json()`까지 단언.
7. **단계별 verify gate**: 보안 critical 슬라이스(M5)는 4단계로 분할 + 각 단계 verify 통과 없이 다음 진입 금지. 사후 회귀 위험 최소화.

---

## 다음 세션이 시작할 때

1. main 머지 절차: worktree에서 `git checkout main && git merge worktree-marketplace-resources` (또는 squash merge PR)
2. 실제 k-skill sync 한 번 실행 → first-wave 등록 검증
3. Frontend polish 진행 → M8b 골격 위에 edge case + 모바일 적응
4. Phase 2 진입 시 Spec §14 Phase 2 부터 시작
