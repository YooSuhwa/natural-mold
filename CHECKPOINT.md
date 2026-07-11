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
- [x] used_by_count 실집계: `skill_response_enrichment.agent_link_counts_by_skill`(단일 GROUP BY, user + `runtime_profile=='standard'` 필터) → `skill_router_support.py` serialize 주입 + `models/skill.py` stale 주석 갱신 (컬럼은 항상 0 유지 — 회귀 단언 포함)
- [x] 리비전 파일 API: `GET .../revisions/{rid}/files`(zip 메타+8KB sniff, is_binary 마킹) + `.../files/content?path=`(정확 일치, 2MB 상한은 헤더 아닌 스트림 검증, pruned 명시 응답, 404 통일). `SkillRevisionDetail`에 `parent_revision_id`/`restored_from_revision_id` 노출(M4 diff 기준)
- [x] 빌더 세션 목록: `GET /api/skill-builder?skill_id=&status=&limit=` — `SkillBuilderSessionBrief` 경량 응답, source OR finalized 매칭, updated_at desc, limit 1..100
- 검증: 신규 테스트 13개 + 전체 2681 그린(SKILL_EVALUATION_ENABLED=true), ruff 클린. env-false 기인 기존 실패 2건은 env로 확인
- 상태: done (2026-07-11)

## M2a: 스튜디오 셸 + 신규 라우트 + 탭 이식 + 설정 탭 (다이얼로그 병존 착지)
- [x] `SkillStudioShell`(usePathname 파생 — 순수 헬퍼 `_lib/skill-studio-tabs.ts`, 6탭, 컨텍스트 바+스킬 스위처+개선 버튼, flex min-h-0 체인) + `app/skills/layout.tsx` 삽입
- [x] `/skills/[skillId]/{evaluation,versions,source,settings}` + `[skillId]`→source redirect + `/skills/builder` 인덱스(세션 목록+시작 CTA, `useSkillBuilderSessions` 신규)
- [x] 페이지용 탭 셸 렌더러(`renderSkillStudioTabShell`, 4슬롯 overlay 포함) + 평가/버전/소스 이식 + 설정 탭(`skill-settings-sections.tsx` — 메타데이터는 SkillMetadataTab 렌더 프롭 재사용)
- [x] 기존 탭 컴포넌트 페이지 모드: `onClose?` 선택화(history/metadata/text/package) + `showDangerZone`(삭제/내보내기를 설정 탭으로)
- [x] breadcrumb: 탭 세그먼트 라벨 4종(nav.skill*) + `SkillName` 리졸버 + 빌더 세션 uuid skip, i18n `skill.studio.*` ko/en
- 검증: vitest 1274 / tsc / build(신규 라우트 6종 등록) / eslint·i18n 그린. design-system·a11y 레드는 **베이스 기존**(stash 대조 검증 — approval-card/artifact-panel 드리프트, 내 diff 밖). 신규 위반 0(settings 내보내기 앵커 aria-label, package-footer 베이스라인 라인이동 2건만 갱신). E2E `skill-builder-chat.spec.ts` 무수정 2/2(18.9s, throwaway 5437/3410/8410)
- 상태: done (2026-07-11)

## M2b: 레거시 절체 + 다이얼로그 제거
- [x] `app/skills/page.tsx` 서버 redirect — `legacyDetailTabToStudioTab`(content→source / credentials·metadata→settings / history→versions), 영구 안전망
- [x] 콜사이트 4곳: marketplace-card / install-wizard / skill-builder-rail(→`/skills/{id}/source`) / 백엔드 `SKILL_DETAIL_DEEPLINK`(+finalize pytest 13 그린)
- [x] skills-page-client의 detailId state/`replaceDetailUrl`/`openBuilderImprove`(컨텍스트 바로 이관) 제거, SkillDetailDialog+테스트 삭제(Create/Publish 유지), `tests/pages/skills.test.tsx`를 클라이언트 컴포넌트 렌더로 전환(async 서버 페이지는 RTL 불가)
- [x] E2E 갱신: skill-history(레거시 redirect 검증 포함)·skill-export(설정 탭)·skill-evaluation-actions ×2(자격증명 연결→설정 탭 이동) + captures 2종 waitForURL(`/skills/[^/]+/source`). **스테일 스펙 2종 삭제**(skill-builder-preview/conflict — origin/main에서도 레드, Phase 1 제거된 다이얼로그 UI 테스트; 충돌 커버리지는 skill-builder-chat.spec §2-3이 대체)
- 노트: skill-builder-create/readiness.spec도 origin/main부터 레드(구 다이얼로그 참조, detailId 무관) — M2b 범위 밖, M5 스윕에서 삭제/재작성 판단
- 검증: vitest 1270 / tsc / eslint / i18n / build 그린 + 갱신 E2E 6/6(mock 모드) + finalize pytest
- 상태: done (2026-07-11)

## M3: 목록 표 + 벌크 삭제
- [x] `skill-list-table.tsx` DataTable 전환 — 컬럼: 이름+slug/종류/상태(HealthBadge)/평가(SummaryBadge)/연결(실카운트)/수정일/행 액션(수정→improve·평가·버전 + 메뉴: 소스·게시·내보내기·삭제). 헤더에 "패키지 업로드" 버튼 추가(목업). SkillCard 고아 삭제
- [x] 기존 툴바 유지(kind 탭+상태 칩+검색 → 필터 배열 주입, `searchable=false`), 행 클릭→소스 탭
- [x] `enableRowSelection`(첫 도입) + 벌크 바(toolbar 슬롯, testid skill-bulk-bar) + **tableEpoch key remount 리셋** + 확인 다이얼로그 이름 열거+연결 경고 + 순차 삭제/부분 실패 토스트
- [x] `skill.columns` 재활용(+status/evaluation 신규), `skill.studio.list.*` i18n, 유닛(벌크 플로우 포함 6) + 벌크 E2E 신규(skills-management.spec — 선택→확인→삭제→리셋 단언)
- 검증: vitest 1270 / tsc / eslint(내 파일 클린) / i18n / a11y·design-system 신규 위반 0 / E2E skills-management 2·skill-state-filters 1 그린
- 상태: done (2026-07-11)

## M4: 버전 diff + 리비전 소스 보기
- [x] jsdiff(`diff`) 의존성 + 순수 diff 유틸(`skill-revision-diff-lines.ts`, 유닛 4) + `SkillRevisionDiffCard`(색상은 기존 `moldy-status-{success,danger} moldy-status-soft` 시맨틱 클래스 — 신규 CSS 0)
- [x] SkillHistoryTab 우측 컬럼에 diff 카드(선택 rev vs `parent_revision_id`; 최초=전체 추가+배지, pruned/404/parent 유실=placeholder). 프론트 타입·API·훅(files/fileContent, staleTime Infinity+retry:false) 추가
- [x] "이 버전 소스 보기" → `/skills/{id}/source?revision={rid}` — `SkillRevisionSourceViewer`(파일 목록+read-only 뷰어, 바이너리 비활성 표기, pruned placeholder, 버전 관리/현재 버전 복귀)
- 검증: vitest 1274 / tsc / build / i18n 그린, skill-history E2E 확장(diff 라인 + read-only 뷰어 + 저장 버튼 부재 단언) 통과
- 상태: done (2026-07-11)

## M5: 고아 스윕 + 스튜디오 E2E/캡처 + 문서
- [x] 고아 스윕: `skill-detail-tabs.tsx`+테스트 **통삭제**(coerceSkillDetailTab 포함 — redirect는 `_lib`의 `legacyDetailTabToStudioTab`이 담당하므로 소비자 0), `skill.detailDialog` 고아 키 3건만 제거(unsupported/improveByChat/history.compatibility — 142키 전수 스캔), 스테일 E2E 2종 추가 삭제(skill-builder-create/readiness — origin/main부터 레드, Phase 1 제거된 다이얼로그 UI 전용)
- [x] 스튜디오 E2E `skill-studio.spec.ts` 3케이스(표 행 내비+탭 비활성/스위처 탭 유지/빌더 인덱스) + 캡처 투어 `captures-skill-studio.spec.ts` 7장(목록/벌크/소스/버전 diff/리비전 뷰어/설정/빌더 인덱스 — 라이브 스택 통과)
- [x] 문서: phase1 스펙 §10 출시 표기
- 백로그 추가: System LLM 미설정 안내 E2E 재작성(현 SkillCreateDialog 계약 기준 — 삭제된 readiness spec의 커버리지 공백)
- 검증: backend 2681(SKILL_EVALUATION_ENABLED=true)+ruff / vitest 1269 / build / eslint 0에러 / i18n 그린. 라이브 스택(5437/3410/8410): skill-builder-chat 2 + captures-phase15 3 + captures-skill-studio 1(7장) 통과. mock 모드: skill-studio 3·history·export·eval-actions 2·management 2·state-filters 그린. design-system·a11y 레드는 베이스 기존(stash 대조), architecture 보고-전용 +2는 기존 exportUrl 직수입 클래스와 동일(주석 검토 완료)
- 상태: done (2026-07-11)

## 마일스톤 의존
M0 → M1 → M2a → M2b → M3 → M4 → M5. 이후 /review 적대 리뷰 → PR.

---

## (보존) Phase 1.5 잔여 백로그
- 레일 소스 뷰 파일 목록에 바이너리 asset 미표시(표시 계층 fail-closed 유지 — 필요 시 목록만 노출+내용 404)
- improve 충돌 re-seed, 리로드 동의 플래그, dead 세션 대화 재생성
- 리로드-중-첫POST 극소 창의 2차 자동발화 시도(서버 unique active run 제약이 409로 거부해 실중복은 불가 — 서버측 first-message idempotency로 수렴 가능)
- Phase 2에서 추가된 백로그: 일괄 패키지 내보내기(D3 드랍), 스킬 복제(목업 행 메뉴), used_by_count 컬럼 제거(마이그레이션 필요), 스킬 축 usage/cost 데이터 소스(Phase 3 선행 작업)
