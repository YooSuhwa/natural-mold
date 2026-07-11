# CHECKPOINT — 스킬 스튜디오 Phase 2: 6탭 풀페이지 스튜디오

> 이전 내용(Phase 1 빌더 챗 + P1.5)은 완료·머지되어 교체함 (PR #285, #289).
> Phase 1.5 잔여 백로그는 문서 하단에 보존.

스펙: `docs/design-docs/skill-studio-phase2-studio-spec.md`
브랜치: `feature/skill-studio-phase2` (worktree `.claude/worktrees/feature+skill-builder-chat`, origin/main fe6a8502 기준)
원칙: 마일스톤 완료마다 커밋. push 검증 시 `SKILL_EVALUATION_ENABLED=true`. 마이그레이션 0건 목표.

## M0: 브랜치 + 스펙 + CHECKPOINT
- [x] origin/main 기준 브랜치 생성, worktree .env symlink 확인
- [x] Phase 2 스펙 문서 작성 (제품 결정 D1~D3 반영)
- [x] CHECKPOINT 교체 전개
- 상태: done (2026-07-11)

## M1: 백엔드 3종 (집계 / 리비전 파일 / 세션 목록)
- [ ] used_by_count 실집계: `skill_response_enrichment.py` 배치 함수(단일 GROUP BY, user + `runtime_profile=='standard'` 필터) → `skill_router_support.py` serialize 주입 + `models/skill.py` stale 주석 갱신
- [ ] 리비전 파일 API: `GET .../revisions/{rid}/files` + `.../files/content?path=` (정확 일치, 8KB sniff, 2MB cap, pruned 명시 응답, 404 통일)
- [ ] 빌더 세션 목록: `GET /api/skill-builder?skill_id=&status=&limit=` (user 스코프, source OR finalized 매칭, updated_at desc)
- 검증: `cd backend && uv run pytest -q -k "skill" && uv run ruff check .`
- done-when: 신규 테스트(링크 0/N·타유저·히든 제외 / 소유권·바이너리·pruned·경로 404 / 스코프·매칭·정렬) 그린
- 상태: pending

## M2a: 스튜디오 셸 + 신규 라우트 + 탭 이식 + 설정 탭 (다이얼로그 병존 착지)
- [ ] `SkillStudioShell`(클라이언트: useParams+usePathname 파생, 6탭, 컨텍스트 바+스킬 스위처, `flex min-h-0 flex-1 flex-col`) + `app/skills/layout.tsx` 삽입
- [ ] `/skills/[skillId]/{evaluation,versions,source,settings}` + `[skillId]`→source redirect + `/skills/builder` 인덱스
- [ ] 페이지용 탭 셸 렌더러(4슬롯, overlay 필수) + 평가/버전/소스 이식 + 설정 탭(바인딩+메타데이터+게시/내보내기/삭제 — 에디터에서 추출)
- [ ] breadcrumb 라벨 + 스킬명 리졸버, i18n `skill.studio.*` (ko 정본 + en 미러)
- 검증: `pnpm vitest run && pnpm build && pnpm lint && pnpm lint:i18n && pnpm lint:design-system && pnpm lint:a11y` + E2E `skill-builder-chat.spec.ts` 무수정 통과(회귀 게이트)
- 상태: pending

## M2b: 레거시 절체 + 다이얼로그 제거
- [ ] `app/skills/page.tsx` 서버 redirect (`coerceSkillDetailTab` 재사용, content→source / credentials·metadata→settings / history→versions)
- [ ] 콜사이트 4곳: marketplace-card / install-wizard / skill-builder-rail / 백엔드 `SKILL_DETAIL_DEEPLINK`(+finalize pytest)
- [ ] skills-page-client의 detailId state/`replaceDetailUrl` 제거, SkillDetailDialog 제거(Create/Publish 다이얼로그 유지)
- [ ] E2E 8곳 갱신 (waitForURL 정규식 포함: captures 2종)
- 검증: 해당 E2E spec 개별 실행 + `uv run pytest tests/test_skill_builder_finalize.py`
- 상태: pending

## M3: 목록 표 + 벌크 삭제
- [ ] DataTable 전환(models 페이지 선례) — 컬럼: 이름+slug/종류/상태/평가/연결(실카운트)/수정일/행 액션(수정→improve·평가·버전 + 메뉴: 소스·내보내기·삭제)
- [ ] 기존 툴바 유지(kind 탭+상태 칩+검색 → 필터 배열 주입, `searchable=false`)
- [ ] `enableRowSelection` + 벌크 바(toolbar 슬롯) + **key remount 리셋** + 이름 열거 확인 + 연결 카운트 경고 + 순차 삭제/부분 실패 토스트
- [ ] 고아 i18n 키 재활용(`skill.columns`/`skill.deleteConfirm`), 벌크 E2E 신규
- 검증: vitest + `pnpm lint:design-system` + 벌크 E2E
- 상태: pending

## M4: 버전 diff + 리비전 소스 보기
- [ ] jsdiff 의존성 + SKILL.md 라인 diff 렌더러(`--status-success/danger` 토큰만)
- [ ] versions 페이지 diff 카드(선택 rev vs parent, 최초/pruned placeholder)
- [ ] "이 버전 소스 보기" → `/skills/{id}/source?revision={rid}` read-only 모드
- 검증: diff util vitest + skill-history E2E 확장
- 상태: pending

## M5: 고아 스윕 + 스튜디오 E2E/캡처 + 문서
- [ ] 고아 스윕: `getVisibleSkillDetailTabs`(+테스트), `skill.detailDialog` **개별 고아 키만**(네임스페이스 통삭제 금지 — 이식 탭 15개 파일 공유), `coerceSkillDetailTab`은 유지
- [ ] 스튜디오 E2E spec(표/벌크/탭 내비/스위처/diff/레거시 redirect) + 캡처 투어
- [ ] 문서: phase2 스펙 체크오프, phase1 스펙 §10 참조 갱신
- 검증: backend 전체 pytest + `pnpm vitest run && pnpm build && pnpm lint && pnpm lint:i18n && pnpm lint:design-system && pnpm lint:a11y && pnpm lint:frontend-architecture` + E2E throwaway 스택(fresh 포트, `E2E_LLM_*=''`, `E2E_SEED_USER_ENABLED=true`)
- done-when: 스펙 §2 성공 기준 6건 전부
- 상태: pending

## 마일스톤 의존
M0 → M1 → M2a → M2b → M3 → M4 → M5. 이후 /review 적대 리뷰 → PR.

---

## (보존) Phase 1.5 잔여 백로그
- 레일 소스 뷰 파일 목록에 바이너리 asset 미표시(표시 계층 fail-closed 유지 — 필요 시 목록만 노출+내용 404)
- improve 충돌 re-seed, 리로드 동의 플래그, dead 세션 대화 재생성
- 리로드-중-첫POST 극소 창의 2차 자동발화 시도(서버 unique active run 제약이 409로 거부해 실중복은 불가 — 서버측 first-message idempotency로 수렴 가능)
- Phase 2에서 추가된 백로그: 일괄 패키지 내보내기(D3 드랍), 스킬 복제(목업 행 메뉴), used_by_count 컬럼 제거(마이그레이션 필요), 스킬 축 usage/cost 데이터 소스(Phase 3 선행 작업)
