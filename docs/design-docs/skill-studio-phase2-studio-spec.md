# SPEC — 스킬 스튜디오 Phase 2: 6탭 풀페이지 스튜디오

> Phase 1 스펙: `skill-studio-phase1-builder-chat-spec.md` (§10에서 본 범위를 예약).
> 디자인 정본: `~/Downloads/Web-Prototype_skill/skill-studio.html` (repo 외부 static 목업).
> 작성: 2026-07-11, 브랜치 `feature/skill-studio-phase2` (origin/main fe6a8502 기준).

---

## 1. 배경과 목표

### 1.1 현재 상태 (문제)

- `/skills`는 단일 페이지(카드 그리드) + `?detailId=&tab=` 쿼리 다이얼로그(5탭:
  content/credentials/evaluation/history/metadata) 구조. 평가·버전·소스가 전부
  다이얼로그 폭에 갇혀 있다.
- 목록은 카드 그리드뿐 — 표/정렬/다중 선택/벌크 작업 없음.
- `Skill.used_by_count`는 생성 시 0으로만 쓰이고 동기화가 없어 **항상 0**
  (연결 카운트 데이터가 사실상 부재).
- 버전 탭은 changed_files 텍스트 요약만 있고 라인 diff가 없다. 리비전 zip의
  파일 내용을 읽는 API도 없다.
- Phase 1에서 빌더 챗은 `/skills/builder/[sessionId]` 풀페이지로 이관 완료 —
  나머지 관리 표면만 다이얼로그에 남았다.

### 1.2 목표

목업의 5탭 스튜디오 IA를 실라우트로 구현하되, 목업이 누락한 기능(자격증명
바인딩·메타데이터 편집·게시)을 6번째 "설정" 탭으로 보존한다:

```
목록(표+벌크삭제) / 빌더(기존 챗) / 평가 / 버전(+SKILL.md diff) / 소스(직접 편집) / 설정
```

공유 요소: 스킬 컨텍스트 바(이름/상태/slug/버전 + 통과율/연결 스탯 + 스킬 스위처).

### 1.3 확정된 제품 결정 (2026-07-11 사용자)

| # | 결정 | 내용 |
|---|------|------|
| D1 | 설정 탭 신설 | credentials 바인딩 + 메타데이터 + 게시/내보내기/삭제를 6번째 탭으로. 목업 5탭 대비 의도적 각색 |
| D2 | 소스 직접 편집 유지 | 기존 Package/Text 에디터 이식(저장=리비전). 목업의 "빌더에서 편집" 버튼 병행 |
| D3 | 벌크=삭제만 | 다중 선택 + 일괄 삭제(연결 에이전트 경고 포함). 목업 bulk-bar의 "패키지 내보내기"는 의도적 드랍(백로그) |

### 1.4 범위 밖 (Phase 3 — 가짜 데이터 금지 원칙)

- with/without A/B 벤치마크 비교 차트, 휴먼 피드백 UI, 비용 실회계, 버전별 통과율
- 스킬 복제(목업 행 메뉴에 존재 — 백로그)
- 일괄 패키지 내보내기(D3)

### 1.5 목업 대비 의도적 각색 (드리프트 기록)

구현이 목업(`skill-studio.html`)과 다른 지점은 전부 의도적 각색이며, 여기 기록해
둔다 (D1~D3 외 추가분):

| 목업 요소 | 구현 | 사유 |
|---|---|---|
| 목록 상단 stat-strip (전체/검증됨/실패 카운트 카드 4장) | `CountedLineTabs`(kind별 카운트) + `SkillStateFilterChips`(상태별 카운트 칩)로 대체 | 같은 정보를 기존 필터 UI가 카운트로 이미 노출 — 클릭 불가한 카드 4장은 중복. 기존 필터 동선 유지(성공 기준 1) |
| 전 컬럼 정렬 | 평가(pass_rate)·수정일만 정렬 | 이름/종류/상태는 kind 탭·상태 칩·검색이 이미 담당. 정렬 축은 실사용 가치가 있는 축만 |
| 'ready' 상태 필터 프리셋 | 미구현 (백로그) | 상태 칩이 health.state 전 종을 카운트로 노출 — 별도 프리셋은 Phase 3에서 사용 데이터 보고 결정 |

---

## 2. 성공 기준 (검증 가능)

1. `/skills`가 표(DataTable)로 렌더되고 다중 선택→일괄 삭제(이름 열거 확인 +
   연결 에이전트 경고)가 동작한다. 기존 kind 탭/상태 칩/검색 필터 유지.
2. `/skills/{id}/evaluation|versions|source|settings` 직접 진입이 동작하고,
   6탭 + 컨텍스트 바(실데이터 연결 카운트 포함)가 모든 스킬 스코프 라우트에 뜬다.
   스킬 스위처로 전환 시 활성 탭이 유지된다.
3. 목록 연결 카운트가 실집계다(에이전트 연결/해제 시 반영, 히든 에이전트 제외).
4. 버전 탭에서 리비전 선택 시 SKILL.md 라인 diff가 렌더되고(최초/pruned는
   placeholder), "이 버전 소스 보기"로 read-only 리비전 소스를 볼 수 있다.
5. 레거시 `/skills?detailId=X&tab=Y` 진입이 새 라우트로 redirect된다
   (marketplace/빌더 레일/finalize 딥링크 포함).
6. 기존 `skill-builder-chat.spec.ts`가 무수정 통과(빌더 라우트/기능 회귀 없음)
   하고, 전체 스위트(backend pytest, vitest, lint 5종, build, 관련 E2E)가 그린.

---

## 3. 아키텍처 결정

### AD-1. 라우팅 = 실라우트 세그먼트, 레거시는 서버 redirect

```
/skills                                → 목록 탭
/skills/[skillId]                      → /skills/[skillId]/source redirect
/skills/[skillId]/{evaluation,versions,source,settings}
/skills/builder                        → 빌더 인덱스(세션 목록 + 시작 CTA)
/skills/builder/[sessionId]            → 빌더 탭 (Phase 1 라우트 불변)
```

- `builder`는 정적 세그먼트라 `[skillId]`와 충돌하지 않는다.
- 레거시 redirect는 `app/skills/page.tsx`(서버)에서 `await searchParams` 후
  `redirect()`. 탭 매핑: `content→source, credentials→settings,
  evaluation→evaluation, history→versions, metadata→settings`
  (`coerceSkillDetailTab` 재사용). next.config redirects가 아닌 코드 응집 선택.
  영구 안전망으로 잔류(외부 도메인의 미래 재발 대비).

### AD-2. 스튜디오 셸 = layout의 클라이언트 컴포넌트, 훅으로 세그먼트 파생

- Next.js에서 layout은 하위 세그먼트 params에 접근 불가(문서 확인) —
  `SkillStudioShell`(클라이언트)이 `useParams()` + `usePathname()`으로 탭
  활성/컨텍스트를 파생한다. jotai 불필요.
- 컨텍스트 바 데이터: 스킬 라우트는 `useSkill(skillId)`, 빌더 라우트는
  `useSkillBuilderSession(sessionId)` → `source_skill_id ?? finalized_skill_id`
  → `useSkill` 체인(TanStack 캐시 공유). create 모드 세션은 "새 스킬 초안" 표기.
- 셸은 `flex min-h-0 flex-1 flex-col` — 빌더 챗 내부 스크롤 계약
  (`app-layout.tsx` → `skill-builder-chat-client.tsx` flex 체인) 유지 필수.
- `ScopedIntlProvider` 네임스페이스 변경 불필요: 셸 문구는 `skill.studio.*`
  (skill 네임스페이스는 /skills 스코프와 빌더 스코프 양쪽에 포함됨).

### AD-3. 탭 이식 = 기존 4슬롯 렌더 프롭 계약에 페이지 렌더러 주입

- `SkillDetailTabRender`(body/footer/sidebar/overlay —
  `components/skill/skill-detail-tab-shell.tsx`)의 DialogShell 렌더러를
  페이지 레이아웃 렌더러로 교체하는 방식. **overlay 슬롯 필수 렌더**
  (SkillHistoryTab 롤백 확인 다이얼로그).
- 평가=`SkillEvaluationTab`(무의존, 풀폭 grid 조정) / 버전=`SkillHistoryTab`
  (footer close 제거) / 소스=`PackageSkillEditor`+`TextSkillEditor`(footer→
  페이지 툴바, 삭제/내보내기 로직은 에디터에서 추출) / 설정=바인딩 패널+
  메타데이터 폼+`PublishWizard`+내보내기/삭제.
- 풀페이지는 6탭 상시 노출 + 각 탭 빈 상태 위임(`getVisibleSkillDetailTabs`
  조건부 숨김은 페이지에서 폐기 — URL 직접 진입 가능하므로).

### AD-4. 백엔드 신규 = 집계 1 + 라우트 2, 마이그레이션 0건

1. **연결 카운트**: `skill_response_enrichment.py`에 배치 함수(단일 GROUP BY,
   `Agent.user_id` + `runtime_profile == 'standard'` 필터 — 히든 규칙 준수) →
   serializer에서 `used_by_count` 덮어쓰기. 컬럼 제거는 하지 않음(마이그레이션
   0건 유지, stale 주석만 갱신). 연결 에이전트 **이름 목록**은 신규 API 없이
   프론트 `useAgents()`의 `Agent.skills`(SkillBrief)에서 역도출.
2. **리비전 파일**: `GET .../revisions/{rid}/files`(zip namelist+size) +
   `GET .../revisions/{rid}/files/content?path=`(정확 일치, 8KB 바이너리
   sniff + 2MB cap — `skill_draft_workspace.py` 상수 패턴 재사용). zip은
   `_read_revision_bytes` 재사용, 디스크 추출 없음. `snapshot_pruned` 명시
   응답. 404 통일(enumeration-safe).
3. **빌더 세션 목록**: `GET /api/skill-builder?skill_id=&status=&limit=` —
   user 스코프, `source_skill_id==X OR finalized_skill_id==X`(create 모드
   세션도 포착), `updated_at desc`(기존 인덱스).

### AD-5. 목록 표 = DataTable 재사용 + 기존 필터 툴바 유지

- `components/ui/data-table.tsx` + `settings/models/page.tsx` 선례.
  기존 `CountedLineTabs`/`SkillStateFilterChips`/`SearchInput`/`filterSkillList`
  툴바를 유지하고 필터된 배열을 주입(`searchable=false`, toolbar 슬롯=벌크 바).
- DataTable 함정 대응: `rowSelection`은 내부 state → 벌크 삭제 후 **key
  remount로 리셋**; select-all은 페이지 스코프(선례 유지); 검색으로 숨은 선택
  잔존 → 확인 다이얼로그에 선택 스킬 이름 열거.
- 벌크 삭제 = 기존 `DELETE /api/skills/{id}` 순차 호출 + 부분 실패 요약 +
  `skillQueryKeys.all` invalidate. `AgentSkillLink`는 CASCADE라 연결 경고 필수.

### AD-6. 버전 diff = 프론트 계산(jsdiff), 백엔드는 원문만

- `diff`(jsdiff) 의존성 추가, SKILL.md 라인 diff 렌더러는 시맨틱 상태 토큰만
  (`--status-success/danger`) 사용(디자인 가드).
- 기준 = 선택 리비전 vs parent 리비전. 최초(parent 없음)/pruned는 placeholder
  (`SkillRevisionDetail.metadata_json`으로 프론트 사전 판별).
- "이 버전 소스 보기" → `/skills/{id}/source?revision={rid}` — 소스 탭
  read-only 모드(리비전 파일 API 소비, 저장/삭제 비활성, 현재 버전 복귀 버튼).

---

## 4. 레거시 `detailId` 생성처 전수 (전부 갱신)

| 위치 | 파일 |
|------|------|
| 프론트 | `components/marketplace/marketplace-card.tsx`, `components/marketplace/install-wizard.tsx`, `app/skills/builder/[sessionId]/_components/skill-builder-rail.tsx` |
| 백엔드 | `app/services/skill_builder_finalize.py` `SKILL_DETAIL_DEEPLINK` (+`tests/test_skill_builder_finalize.py`) |
| E2E | `skill-builder-preview` `skill-history` `skill-builder-conflict` `skill-evaluation-actions`(×2) `skill-export` + captures 2종(`waitForURL(/\/skills\?detailId=/)`) |

---

## 5. 테스트 계획

- **백엔드**: 집계(링크 0/N, 타 유저 제외, 히든 profile 제외), 리비전 파일
  (소유권 404 통일, 바이너리 skip, pruned, 경로 불일치 404), 세션 목록
  (user 스코프, source/finalized 매칭, 상태 필터, 정렬), finalize 딥링크 변경.
- **vitest**: diff util, 스튜디오 셸 탭 파생, 벌크 선택/삭제 플로우,
  이식 탭 렌더러(overlay 포함), redirect 매퍼.
- **E2E**: 기존 skill 관련 spec 8+ 갱신, 스튜디오 신규 spec(표/벌크/탭 내비/
  스위처/버전 diff/레거시 redirect), 캡처 투어. `skill-builder-chat.spec.ts`는
  무수정 통과가 회귀 게이트. throwaway 스택(fresh 포트, `E2E_LLM_*=''`,
  `E2E_SEED_USER_ENABLED=true`, scripted 검증 전 DB 재생성).

---

## 6. 구현 마일스톤 (CHECKPOINT.md로 전개)

| M | 내용 | done-when |
|---|------|-----------|
| M0 | 브랜치 + 스펙 + CHECKPOINT | 스펙 커밋 |
| M1 | 백엔드 3종 + pytest | `pytest -k skill` 그린, ruff 클린 |
| M2a | 셸 + 라우트 + 탭 이식 + 설정 탭 (다이얼로그 병존) | vitest/tsc/lint/build + 빌더 E2E 회귀 게이트 |
| M2b | 레거시 절체 + 다이얼로그 제거 + E2E 갱신 | 해당 E2E + finalize pytest 그린 |
| M3 | 목록 표 + 벌크 삭제 | vitest + 벌크 E2E + design guard |
| M4 | 버전 diff + 리비전 소스 보기 | diff 테스트 + skill-history E2E 확장 |
| M5 | 고아 스윕 + 스튜디오 E2E/캡처 + 문서 | §2 성공 기준 전부, 전체 스위트 그린 |

## 7. 리스크와 완화

| 리스크 | 완화 |
|--------|------|
| 빌더 챗 뷰포트 붕괴(셸 flex 체인) | M2a에서 빌더 E2E 회귀 게이트 |
| E2E 광역 파손(8+ spec) | 마일스톤별 배치 갱신(M2b/M3) |
| 벌크 삭제 후 stale 선택(DataTable 내부 state) | key remount + 이름 열거 확인 |
| i18n 과잉 스윕 → raw key 렌더(`skill.detailDialog.*`는 이식 탭 15개 파일 공유) | 키 단위 grep 후 제거, 네임스페이스 통삭제 금지 |
| 리비전 zip 대용량/바이너리 | 2MB cap + 8KB sniff 재사용, pruned 명시 응답 |
