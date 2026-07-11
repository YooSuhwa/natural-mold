# Moldy 전체 리팩토링 계획 + 신규 기능 발굴 (2026-07-07)

> **문서 목적**: 이 문서 하나만 보고 처음부터 끝까지 리팩토링을 수행할 수 있도록, 항목별 근거(file:line)·문제점·단계별 방안·검증 커맨드·공수를 기록한다.
> **분석 방법**: 도메인별 병렬 분석(백엔드 구조/성능/중복, 프론트 구조·중복/성능·디자인, 인프라/DevX, 기능 갭). 모든 항목은 **실제 코드를 열어 확인한 file:line 근거**만 채택했고, 추측성 항목은 배제했다. 라인 번호는 2026-07-07 main(`828d056a`) 기준 — 시간이 지나면 어긋날 수 있으니 함수/심볼명으로 재탐색할 것.
> **분석 시점 규모**: backend/app 84,902줄(라우터 68, 서비스 84, 모델 39), frontend/src 100,620줄(컴포넌트 342), Alembic 리비전 76(M63), backend 테스트 파일 267, e2e 스펙 69.

---

## 0. 총평

이 코드베이스는 **분해·공통화 역량이 이미 증명된 상태**다 — `conversation_agent_protocol*` 18파일 분해, executor split, `DialogShell` 38곳 채택, `lib/query-keys` 팩토리, 디자인 토큰 린트 가드가 그 증거다. 진짜 문제는 능력 부족이 아니라 **일관성 부족**이다:

1. **적용 안 된 곳이 남았다** — `chat_service.py`(1,786줄), `install_service.py`(1,366줄), `use-moldy-langgraph-stream.ts`(2,941줄) 같은 갓 모듈, 서비스 레이어가 아예 없는 MCP/tools/models 라우터.
2. **half-done 공통화** — 에러 팩토리(`error_codes.py`)·쿼리키 팩토리·`make_user` 픽스처·`BaseDetailDialog`가 **이미 존재하는데** 절반만 채택돼 드리프트가 진행 중.
3. **hot path 성능 부채** — 폴링당 N+1(3계열), 이벤트 루프를 250ms 세우는 bcrypt, SSE 이벤트당 이중 redaction.
4. **이중 시스템** — 프론트 채팅 legacy/v3 런타임 병렬 유지가 모든 채팅 기능의 비용을 2배로 만든다.
5. **안전망 공백** — CI 파이프라인 부재, 채팅 라우트 에러 바운더리 부재, SSRF 미수정.

---

## 진행 현황 (Progress) — 2026-07-09 갱신

머지된 PR 기준 실제 진행 상태. 세부 항목별 완료 표시는 아래 §1 매트릭스의 각 행에 반영.

| PR | 묶음 | 완료 항목 | 상태 |
|----|------|-----------|------|
| #279 | Phase 0 안전망 | SEC-1, SEC-2, SEC-3, BE-P4, FE-D1, IX-1 + 마스터 계획 문서 | ✅ 머지 |
| #279 | pyright 번다운 A | data/ exclude(970→627) + `docs/pyright-burndown-plan.md` | ✅ 머지 |
| #280 | Phase 1 hot path | BE-P1, BE-P3, BE-P6, BE-P7, **BE-P5 부분**((a)(c) 완료) | ✅ 머지 |
| #281 | Phase 2 중복정리 | **BE-D2**(에러 팩토리), **BE-D4**(visibility 술어), CI 통합테스트 분리 | ✅ 머지 |
| #282 | Phase 2 소유권 | **BE-D1 부분**(owned_conversation 의존성 + 10/30 라우터: artifacts·files·traces) | ✅ 머지 |
| #283 | 린트 하드닝 계획 | `docs/lint-hardening-plan.md` + lint:all 스크립트 | ✅ 머지 |
| #287 | 린트 A-1 | 가드 예외 3건 등록(+회귀 테스트) + 그린 가드 4개 CI·pre-commit 연결 (a11y·design-system·frontend-architecture strict는 A-2 잔여) | ✅ 머지 |

**부분완료 잔여** (재개 지점):
- **BE-P5**: (b) 이중 redaction, (d) seen_event_ids, (e) inline flush — redaction 의미론·flush 순서 얽힘, 별도 PR
- **BE-D1**: 나머지 20곳 — conv 객체 재사용 라우터(branches/crud/messages)는 `conv: Conversation = Depends(...)` 주입 방식으로 핸들러별 검증; run_cancel/ag_ui/runs/followup 게이트는 mutation 순서 확인; `conversation_agent_protocol_runtime`은 헬퍼라 제외

**미착수 P1** (다음 우선): BE-P2(메시지 페이지네이션·FE연동), BE-S2(MCP/tools/models 서비스레이어), BE-S7(OAuth 분리), BE-S1(chat_service 분해), BE-S3(install_service 분해), FE-S1(런타임 수렴), FE-S2(2941줄 훅), FE-P1(컨텍스트 churn), FE-P2(가상화). **미착수 P2/P3**: §1 매트릭스에서 ✅/🔶 없는 행 전부.

**별도 트랙**: pyright 번다운 B/C/D(`docs/pyright-burndown-plan.md`), 린트 하드닝 A~G(`docs/lint-hardening-plan.md`).

---

## 1. 통합 우선순위 매트릭스

우선순위 정의 — **P0**: 보안·신뢰성, 이번 주 내 착수. **P1**: 사용자 체감/개발 속도에 직접 효과, 1~2 스프린트. **P2**: 데이터 증가·기능 추가에 따라 악화되는 부채. **P3**: 여유 시. 공수 — S(반나절), M(1~2일), L(3일+).

### P0 — 즉시 (보안·신뢰성·저공수 고효과)

| ID | 제목 | 카테고리 | 공수 |
|----|------|----------|:---:|
| SEC-1 | ✅ web_scraper SSRF 미수정 (§3 참고) — Phase 0 완료 | 보안 | S~M |
| SEC-2 | ✅ rotate_credentials no-progress 무한루프 잔존 — Phase 0 완료 | 신뢰성 | S |
| SEC-3 | ✅ 트리거 run-now 경로 중복실행 가드 부재 — Phase 0 완료 | 신뢰성 | S~M |
| BE-P4 | ✅ bcrypt가 이벤트 루프 250ms 블로킹 → `asyncio.to_thread` — Phase 0 완료 | 성능 | S |
| FE-D1 | ✅ 채팅/공유/대시보드 라우트 에러 바운더리 전무 — Phase 0 완료 | 신뢰성 | M |
| IX-1 | ✅ CI 파이프라인 부재 — Phase 0 완료 (pyright는 968 기존 에러로 non-blocking 잡) | DevX | M |

### P1 — 임팩트 최대 (1~2 스프린트)

| ID | 제목 | 카테고리 | 공수 |
|----|------|----------|:---:|
| BE-P1 | ✅ `GET /messages` 폴링 N+1 (interrupt 하이드레이션) — Phase 1 완료 | 성능 | M |
| BE-P3 | ✅ 폴링 경로 MCP credential `FOR UPDATE` N+1 — Phase 1 완료 | 성능 | S~M |
| BE-P5 | 🔶 SSE 이벤트당 중복 비용 — (a) json.dumps·(c) 시크릿 정렬 완료, (b) 이중 redaction·(d) id 재로드·(e) inline flush 잔여 | 성능 | M |
| BE-P2 | `GET /messages` 무제한 로드 → keyset 페이지네이션 | 성능 | L |
| BE-S2 | MCP/tools/models 서비스 레이어 신설 (라우터 raw DB 제거) | 구조 | M |
| BE-S7 | credentials 라우터 OAuth 로직 → oauth_service | 구조 | M |
| BE-S1 | chat_service.py 8-클러스터 분해 | 구조 | L |
| BE-S3 | install_service.py 3-타입 분해 | 구조 | L |
| BE-D1 | 🔶 소유권 조회+404 패턴 30곳 → Depends 의존성 — #282 부분(10/30: artifacts·files·traces), 20곳 잔여 | 중복 | M |
| BE-D2 | ✅ raw HTTPException 21곳 → error_codes 팩토리 통일 — #281 완료 (system_llm_settings는 byte-identical 계약이라 제외) | 중복 | S~M |
| FE-S1 | 채팅 런타임 이중화(legacy/v3) 수렴 1단계 | 구조 | M~L |
| FE-S2 | use-moldy-langgraph-stream.ts(2,941줄) 분해 | 구조 | L |
| FE-P1 | 스트리밍 중 컨텍스트 churn → 전체 메시지 리렌더 | 성능 | M |
| FE-P2 | 채팅 스레드 가상화 (선조치 memo는 S) | 성능 | L |

### P2 — 부채 상환 (백로그 상단)

| ID | 제목 | 카테고리 | 공수 |
|----|------|----------|:---:|
| BE-P6 | ✅ FK 인덱스 5건 (M67) — Phase 1 완료 | 성능 | S |
| BE-P7 | ✅ checkpointer 풀 2/20 + 엔진 풀 설정 노출 — Phase 1 완료 | 성능 | S~M |
| BE-P8 | health_check_history 무한 증가 (retention dead code) | 성능 | S |
| BE-P9 | MCP health 폴링 직렬 → 병렬화+backoff | 성능 | M |
| BE-P10 | 마켓 MCP 설치 툴당 SELECT N+1 | 성능 | S |
| BE-P11 | artifact ingest 파일당 다중 SELECT | 성능 | M |
| BE-P12 | memories 무제한 목록 + 마켓 OFFSET 페이지네이션 | 성능 | M |
| BE-S4 | services↔agent_runtime 양방향 결합 역전 | 구조 | L |
| BE-S5 | write_tools.py 26개 클로저 분해 | 구조 | M |
| BE-S6 | 디렉토리 컨벤션 ADR + services 서브패키징 | 구조 | M |
| BE-S8 | artifact_service.py recorder/library 분해 | 구조 | M |
| BE-S9 | scheduler.py 잡 로직 → 도메인 서비스 이관 | 구조 | M |
| BE-S10 | runtime_component_builder.py 5-관심사 분해 | 구조 | M |
| BE-D3 | audit record_event self-action 래퍼 | 중복 | M |
| BE-D4 | ✅ system-or-owned 술어 8곳 → Tool.visible_to — #281 완료 (`_load_owned` 통합은 BE-D1과 함께 잔여) | 중복 | S~M |
| BE-D7 | 테스트 Model/Agent 팩토리 픽스처 도입 | 중복 | M~L |
| FE-S3 | assistant-thread.tsx(1,458줄) 분해 | 구조 | M |
| FE-S4 | approval-card 분해 + 승인 훅 2계열 통합 | 구조 | M |
| FE-S5 | 비대 page 2종(memory 649/template 617) 분해 | 구조 | M |
| FE-S6 | 다이얼로그 셸 8+회 복붙 → 공용 훅 + dead 추상화 정리 | 중복 | L |
| FE-S7 | lib/types/index.ts 혼합 바렐 해체 | 구조 | M |
| FE-S8 | Query 키 인라인 9개 훅 → 팩토리 이관 (빠른 승리) | 중복 | S |
| FE-P3 | 관리 테이블/네비게이터 가상화 | 성능 | M |
| FE-P4 | 활성 런 1초 이중 폴링 일원화 | 성능 | M |
| FE-D2 | a11y 라벨 26건 (baseline 해소) | 디자인 | M |
| FE-D3 | loading/error/빈 상태 커버리지 균일화 | 디자인 | M |
| FE-D5 | agent-prism 트레이스 UI 영어 전용 (i18n) | 디자인 | M |
| FE-D6 | tool-ui shadcn 우회 + RadioGroup 프리미티브 신설 | 디자인 | M |
| FE-D7 | 미디어 아티팩트 aria-label/캡션 | 디자인 | S |
| IX-2 | pre-commit 훅 부재 | DevX | S |
| IX-3 | docker-compose/Dockerfile 프로덕션 하드닝 | 인프라 | S~M |
| IX-5 | 구조화 로깅 + request-id 부재 | 인프라 | M |
| IX-6 | aiosqlite↔PG 격차 — CI에 PG integration 잡 | 테스트 | S~M |

### P3 — 여유 시

| ID | 제목 | 카테고리 | 공수 |
|----|------|----------|:---:|
| BE-P13 | scrape HTML 파싱/zip export `to_thread` | 성능 | S |
| BE-S11 | marketplace 프로젝션 중복 통합 | 구조 | M |
| BE-D5 | keyset 커서 정규화 공유 + limit 상수 통일 | 중복 | S~M |
| BE-D6 | 도구 러너 인증+HTTP 헬퍼 추출 | 중복 | S |
| BE-D8 | Response 스키마 믹스인 (이득 최소 — 후순위) | 중복 | S |
| FE-S9 | features/ 디렉토리 이주 (도메인 단위 점진) | 구조 | L |
| FE-S10 | openapi-typescript 타입 생성 도입 | 구조 | M |
| FE-P5 | 페이지 'use client' → 서버 셸 분리 (신규 규칙 우선) | 성능 | M |
| FE-P6 | chart.js dead dep 제거 + next/image | 성능 | S |
| FE-P7 | 셀렉터 하드닝 + phase-timeline O(n) 스캔 | 성능 | S |
| FE-D4 | chart-card 팔레트 토큰화 + bg-white 3건 dark 변형 | 디자인 | S |
| IX-4 | Alembic 76 리비전 squash | DevX | M |
| IX-7 | e2e captures/regression playwright 프로젝트 분리 | 테스트 | S |

### Quick Wins (공수 S로 즉시 처리 가능한 것만 모음)

`BE-P4`(bcrypt to_thread) · `BE-P6`(인덱스 마이그레이션 1개) · `BE-P8`(history GC) · `BE-P10`(설치 N+1 hoist) · `BE-P13`(to_thread 2곳) · `BE-D2`(에러 팩토리 치환) · `BE-D6`(러너 헬퍼) · `FE-S8`(쿼리키 이관) · `FE-P6`(chart.js 제거) · `FE-P7`(셀렉터 상수) · `FE-D4`(차트 토큰) · `FE-D7`(미디어 라벨) · `IX-2`(pre-commit) · `SEC-2`(회전 루프 가드)

---

## 2. 권장 실행 로드맵 (Phase)

### ▶ 현 시점 실행 순서 (2026-07-10, 미완료만) — 새 세션은 여기부터

> **사용법**: `/clear` 후 새 세션에서 **"이 문서 실행 순서에서 다음 미완료 항목 진행해줘"** 한 문장이면 된다. 항목을 콕 집으려면 아래 번호의 프롬프트를 그대로 복붙. 공통 규칙: worktree에서 origin/main 기준 새 브랜치, 한 PR = 한 항목, 기능 변화 0(순수 이동은 facade), 검증 그린 후 PR. (백엔드 검증 = `ruff` + `pytest -n 4 --ignore=tests/integration` + `pytest tests/integration -m integration` 직렬(마커 자동부여 후 `-m integration` 필수 — 없으면 전량 deselect: dir-scoped는 exit 5 red, `pytest tests/` 전체 실행에선 조용히 제외됨), 푸시 시 `SKILL_EVALUATION_ENABLED=true`. pyright는 전체 968 백로그라 수정 파일 단위로만.)
> 완료하면 이 목록에서 해당 줄에 ✅와 PR 번호를 남겨 다음 세션이 이어받게 할 것.

**Stage 0 — 자동 게이트 먼저 (이후 모든 작업이 자동 검증받음. 최대 레버리지)**
1. ✅ **린트 A-1** — PR #287. 재측정(i18n 3·type-safety 2·e2e-hygiene 40 = 전부 정당) → 예외 3건 등록 + 예외별 회귀 테스트 → 그린 가드 4개(lint·i18n·type-safety·e2e-hygiene)를 CI 개별 스텝 + lint-staged에 연결. **frontend-architecture는 2차 리뷰에서 거짓 그린 판명**(비-strict 항상 exit 0, strict는 blocking 3건 레드) → a11y(신규4+해소2)·design-system(12)과 함께 A-2 잔여(FE-D2·FE-D4 연동 + strict blocking 수정).
2. ✅ **린트 C** — PR #288. `S` 룰 활성 + 51건(app 43 + scripts/alembic 8) 트리아지. 실수정 2: openwiki sync_repo.py(LLM 제공 --repo-url/--ref 옵션 주입·ext::/file:// transport 차단 + 테스트 22케이스), generate_image.py(S310 scheme 가드). 나머지 오탐 inline noqa + tests/·alembic/ per-file-ignores. 게이트 회귀 테스트(빨간불 주입 + 예외 non-blanket 네거티브) 동봉.
3. ✅ **린트 F·G** — PR #290. F: `PGH` 활성(현 트리 위반 0 — 순수 예방 게이트, bare noqa/blanket type-ignore 금지) + 게이트 회귀 테스트. G: `tests/integration/conftest.py` 자동 마커 훅 + **CI 직렬 스텝 `-m integration` 필수**(마커 부여 후 plain `pytest tests/integration`은 전량 deselect → exit 5 red; 조용한 변종은 full-suite `pytest tests/`에서 형제 테스트 통과가 exit 0으로 가리는 경우 — 리뷰에서 exit code 정정, 최초 실측이 `| tail` 파이프 함정이었음) + 커버리지/deselection 회귀 테스트. m9는 self-skip이라 안전. pre-push의 plain `pytest tests/`에서 integration이 빠지는 건 의도(CI 직렬이 게이트). **알려진 사각지대(pre-existing)**: `tests/test_trace_storage.py`의 integration 마커 테스트 1건은 디렉토리 밖이라 어느 CI 스텝에서도 안 돌고, aiosqlite에선 실행 시 실패 + live PG 주입 인프라도 없는 죽은 테스트 — m9 패턴(INTEGRATION_DATABASE_URL)으로 tests/integration/ 이관이 후속 과제.
4. ✅ **린트 E** — PR #291. 7룰 배치 활성 + 373건 트리아지(문서 실측 66건은 app/ 한정 — tests/ 308건이 실제 대부분). 실수정 ~48(RET 인라인·PTH pathlib(부팅 SSL 경로 포함)·PT011 match=·PT019 usefixtures·PT013/PT006/N806/N814), 전역 ignore N818(도메인 스타일 예외명 18건), per-file `app/**`=PT(라우터 `test_*` 엔드포인트 오탐 17건)·`tests/*`+=SLF001/DTZ/PT017/PT018/N801/N815(관용구·wire mock 267건), inline noqa 14(ssl 패치·ORM stash·tool schema camelCase·로컬 날짜 — 전부 이유 포함). 게이트 회귀 테스트 `test_lint_low_noise_rules.py`(빨간불 + 예외 rule-scoped 증명). 함정 2회 실증: C416 unsafe fix(`dict(rows.all())`)가 pyright 타입 회귀 유발 → `.tuples()`로 해결 / 커밋 훅 재포맷(113→354 insertions) 후 noqa anchor 재확인 필수였음(유지됨).

**Stage 1 — 부분완료 마무리**
5. **BE-D1 나머지** — conv 객체 재사용 라우터(branches/crud/messages 등)를 `conv: Conversation = Depends(owned_conversation)` 주입 방식으로. §6 [BE-D1] 참고.
6. **BE-P5 나머지** — (b) 이중 redaction, (d) run-scoped seen_event_ids, (e) inline flush → create_task. §5 [BE-P5] 참고.

**Stage 2 — 레이어링·경계 (명확한 정답)**
7. **BE-S7** — credentials 라우터 OAuth → oauth_service. §4 [BE-S7].
8. **BE-S2** — MCP/tools/models 서비스 레이어 신설. §4 [BE-S2]. (트랜잭션 정책 = 서비스 flush/라우터 commit 전역 결정)
9. **BE-D3** — audit self-action 래퍼. §6 [BE-D3].
10. **BE-D7** — 테스트 Model/Agent 팩토리 픽스처. §6 [BE-D7].

**Stage 3 — 갓 모듈 분해 (facade 순수 이동, 하나씩)**
11. **BE-S1** — chat_service.py 8-클러스터 분해. §4 [BE-S1]. (#285로 1,810줄, 스킬빌더 코드도 포함)
12. **BE-S3** — install_service.py 3-타입 분해. §4 [BE-S3].
13. **BE-S5 · BE-S8 · BE-S9 · BE-S10** — write_tools / artifact_service / scheduler / runtime_component_builder (각각 별도 PR).

**Stage 4 — 프론트 대형**
14. **FE-P1** — 스트리밍 컨텍스트 churn(전체 메시지 리렌더). §8 [FE-P1]. (독립적·성능 임팩트 커서 프론트 먼저)
15. **FE-S2** — use-moldy-langgraph-stream.ts(2,941줄) 분해. §7 [FE-S2].
16. **FE-S3 · FE-S4** — assistant-thread / approval-card 분해.
17. **FE-S1** — 채팅 런타임 이중화 수렴. §7 [FE-S1]. (설계 난제라 프론트 익숙해진 뒤)

**Stage 5 — 구조 난제 + 페이지네이션**
18. **BE-S4** — services↔agent_runtime 의존 역전(함수-로컬 import 155곳 근본). §4 [BE-S4].
19. **BE-P2** — 메시지 keyset 페이지네이션(FE 연동). §5 [BE-P2].
20. **BE-S6** — 디렉토리 컨벤션 ADR + 서브패키징. §4 [BE-S6].

**Stage 6 — 백로그 (P2/P3 quick win, 병렬 가능)**
- 백엔드 성능: BE-P8·P9·P10·P11·P12·P13 / 프론트 성능: FE-P2(가상화)·P3·P4·P5·P6·P7
- 디자인·a11y: FE-D2~D7 + 린트 A-2(design-system·a11y 가드 연결) / 중복: BE-D5·D6·D8 · FE-S5~S10
- 인프라: IX-3(docker)·IX-5(구조화 로깅)·IX-4(squash)

**Stage 7 — 타입 게이트 (대형, 마지막)**
21. **pyright 번다운** — `docs/pyright-burndown-plan.md` B→C → D(CI `|| true` 제거, 하드 게이트).
22. **린트 D·B** — pyright standard 승격 + 백엔드 커스텀 가드(raw HTTPException 금지 스크립트).

**순서 근거**: Stage 0을 먼저 = 이후 20여 PR이 자동 검증(이번 리팩토링 세션 최대 교훈). BE-S2가 BE-S9의 선행, 갓모듈(3)은 레이어 정리(2) 후 안전. BE-S4는 여러 갓모듈의 함수-로컬 import 냄새 근본이라 분해 후 마무리. 타입게이트(7)는 968 번다운 선행이라 맨 뒤.

---

### 초기 분석 로드맵 (2026-07-07, 참고용)

의존 관계와 리스크를 고려한 순서. 각 Phase는 독립 브랜치/PR 묶음으로 진행하고, Phase 간 순서는 지키되 Phase 내부는 병렬 가능.

- **Phase 0 — 안전망 (1주)**: SEC-1·2·3 + BE-P4 + FE-D1 + IX-1(CI). CI가 먼저 서야 이후 모든 리팩토링 PR이 자동 검증된다. 주의: 전체 pyright는 968개 기존 에러(초기 "통과" 판단은 파이프라인 exit 코드 착오) — ruff+pytest만 하드 게이트, pyright는 non-blocking 잡.
- **Phase 1 — hot path 성능 (1~2주)**: BE-P1 → BE-P3 → BE-P5 → BE-P6 → BE-P7 + Quick Wins 일괄. 전부 소규모 diff라 회귀 리스크 낮고 체감 효과 즉시.
- **Phase 2 — 레이어링·경계 (2주)**: BE-S2 → BE-S7 → BE-D1 → BE-D2 → BE-D4. "명확한 정답"류라 리뷰 부담 적음. 이때 트랜잭션 정책(서비스 flush / 라우터 commit)을 전역 결정.
- **Phase 3 — 갓 모듈 분해 (2~3주)**: BE-S1 → BE-S3 → FE-S2 → FE-S3 → FE-S4 → BE-S5. 전부 facade 기반 순수 이동 전략이라 기능 변화 0을 유지. 병행: FE-P1(컨텍스트 분리).
- **Phase 4 — 이중 시스템 수렴 (2주+)**: FE-S1(런타임 수렴 1단계) → BE-S4(의존 역전) → BE-S9(스케줄러). BE-P2(메시지 페이지네이션)는 FE 소비부 변경과 함께.
- **Phase 5 — 장기 개선 (백로그)**: 가상화(FE-P2/P3), 디자인/a11y 묶음(FE-D2~D7), 테스트 팩토리(BE-D7), 디렉토리 이주(FE-S9, BE-S6), 타입 생성(FE-S10), squash(IX-4).

**공통 가드레일**:
- 리팩토링 PR은 **기능 변화 0** 원칙 — 순수 이동은 facade re-export로 기존 import 경로 보존.
- 병합 전 전체 검증: `cd backend && uv run ruff check . && uv run pyright && uv run --with pytest-xdist pytest -q -n 4` / `cd frontend && pnpm lint && pnpm vitest run && pnpm build`. 채팅 관련 변경은 e2e `chat-*.spec.ts` 추가 실행 (푸시 시 `SKILL_EVALUATION_ENABLED=true` 필요).
- 한 PR = 한 항목. drive-by 리팩토링 금지 (CLAUDE.md Minimal Impact).

---

## 3. 보안·신뢰성 잔존 이슈 — 2026-07-03 감사 추적 (재검증 완료)

이번 분석에서 감사 High 4건의 현재 상태를 코드로 재확인했다. **리팩토링과 별도 트랙으로 최우선 처리 권고.**

### [SEC-1] web_scraper SSRF — **미수정 (STILL PRESENT)**
- **증거**: `backend/app/agent_runtime/tool_factory.py:149-162` — `scrape_url`이 모델(에이전트)이 제공한 URL을 아무 검증 없이 `client.get(url)`. 공유 클라이언트가 `follow_redirects=True`(`:102-106`)라 리다이렉트 경유 SSRF도 가능. `ipaddress`/`is_private`/`169.254`/allowlist 가드 전무 — localhost·RFC-1918·클라우드 메타데이터(`169.254.169.254`) 미차단.
- **수정 방안**: ① URL 파싱 후 scheme http/https만 허용 ② 호스트 resolve 결과가 사설/루프백/링크로컬 IP면 거부 (`ipaddress.ip_address(...).is_private/is_loopback/is_link_local`) ③ 리다이렉트도 각 hop 재검증(httpx event hook 또는 수동 follow) ④ 응답 크기 상한. 기존 `sanitizeExternalUrl`(프론트) 철학과 동일한 서버판.
- **검증**: `http://169.254.169.254/`, `http://localhost:8001/`, 사설 IP로 redirect하는 URL이 전부 차단되는 pytest 추가.

### [SEC-2] rotate_credentials 무한루프 — **부분 수정 (잔존 리스크)**
- **증거**: `backend/app/scheduler.py:235-251` — OFFSET 제거로 원래 문제는 완화됐으나, 한 배치(≥`_ROTATION_BATCH=100`) 전체가 지속 실패하면 동일 행을 재조회하는 no-progress `while True` 잔존(종료 가드 `len(rows) < _ROTATION_BATCH`가 트립 안 됨).
- **수정 방안**: 실패 id를 세션 내 제외 목록에 축적해 다음 fetch에서 `id.notin_(failed)` 필터, 또는 no-progress(연속 2회 동일 id셋) 감지 시 break + 에러 로깅, 또는 max-iteration 캡.
- **검증**: 복호 불가 credential 100+개 시드 후 잡이 종료되는 단위 테스트.

### [SEC-3] 트리거 중복실행 — **부분 수정 (run-now 경로 잔존)**
- **증거**: `backend/app/agent_runtime/trigger_executor.py:80-122` — APScheduler `coalesce=True, max_instances=1` + 리더락으로 스케줄 경로는 방어되나, `routers/triggers.py:167`의 **run-now가 APScheduler를 우회**해 스케줄 실행 in-flight 중 사용자가 run-now를 누르면 이중 실행.
- **수정 방안**: `execute_trigger` 진입 시 트리거 행 `SELECT … FOR UPDATE` + status→running 클레임(이미 running이면 409 반환), 또는 `agent_trigger_runs`에 "in-flight run당 1행" 부분 유니크 인덱스(`WHERE status='running'`)로 DB 레벨 방어.
- **검증**: 동시 run-now 2회 → 1건만 실행되는 동시성 테스트.

### [SEC-4] 프론트 채팅 에러 바운더리 부재 — **유효 (FE-D1로 편입)**
- 두 개 독립 분석이 재확인. 상세와 방안은 §8 FE-D1 참조.

---

## 4. 백엔드 — 구조/아키텍처 (BE-S)

분석 범위: `backend/app/` (읽기 전용). 총 84,902 LOC / Python. 모든 발견은 실제 파일을 열어 확인한 것이며 라인 참조는 검증됨.

**긍정적 기준선 (리팩토링 대상 아님, 참고용):** `routers/conversation_agent_protocol*.py` 18개 파일은 **모범적 분해 사례**다 — 라우트는 `conversation_agent_protocol.py`(6개)/`_sdk.py`(1개)에만 있고 나머지 16개는 순수 헬퍼 모듈(state, replay, resume, redaction, interrupts, event_normalization 등)로 책임 분리됨. 아래 god module들은 "이 팀이 할 줄 아는" 이 패턴을 아직 적용하지 않은 곳들이다. 또한 `services → routers` 역방향 import는 0건(레이어 방향성 자체는 지켜짐).

---

### [BE-S1] `chat_service.py` 갓 모듈 — 7개 이질적 책임이 한 파일(1786줄)에 혼재
- **우선순위 제안**: P1 — 프로젝트 최대 파일이자 채팅/트리거/폴링 hot path의 중심. 변경 빈도·병합 충돌·회귀 리스크가 가장 높다(MEMORY의 W2-3, 시크릿 수집 규칙 등 다수 사고가 이 파일에서 발생).
- **카테고리**: 구조
- **증거**: `app/services/chat_service.py`. `__all__`(56-84)에 24개 export. 실제 함수 클러스터:
  1. **HITL 인터럽트 재구성** `_review_config_for_action`~`_hydrate_pending_interrupt_tool_calls` (104-493, ~20개 프라이빗 함수)
  2. **시크릿 수집/리댁션** `collect_conversation_secret_values`, `_redact_response_tool_calls` (494-602)
  3. **대화 CRUD + keyset 페이지네이션 + 커서 인코딩** `ConversationPageCursor`~`delete_conversation` (623-1030, `_encode/_decode_conversation_cursor` 포함)
  4. **checkpointer 메시지 로딩** `list_messages_from_checkpointer` (1031-1224, 단일 194줄 함수)
  5. **토큰 사용량/가격** `_resolve_agent_model_pricing`, `save_token_usage` (1239-1291)
  6. **첨부 링크** `link_attachments_to_conversation`, `resolve_turn_user_message_id`, `link_attachments_to_message` (1293-1400)
  7. **파일 리스트** `list_conversation_files` (1402-1480)
  8. **에이전트 런타임 컨텍스트 조립** `get_agent_with_tools`, `build_tools_config`, `build_effective_prompt`, `build_agent_skills`, `trigger_blocked_tools_for_agent_tree` (1513-1786)
- **문제점**: 8개 클러스터가 상호 결합이 거의 없는데도 한 파일에 있어 (a) 어떤 변경이든 파일 전체를 재이해해야 하고 (b) `import base64/json/uuid` + 12개 모델 import가 전부 top-level이라 4번(checkpointer)·6번(첨부)은 이미 함수-로컬 deferred import(1024, 1057-1059줄)로 순환 회피 중 — god module이 순환참조까지 유발. (c) read/poll 경로가 무거운 조립 로직과 같은 모듈이라 캐시/성능 튜닝 시 blast radius가 크다.
- **리팩토링 방안**:
  1. 새 패키지 `app/services/chat/` 생성, 기존 `chat_service.py`는 **facade**로 남겨 `__all__` 재-export(하위호환: 트리거 executor·conversations 라우터가 public shape에 의존 — 파일 docstring 9-12줄 명시).
  2. `chat/interrupts.py` ← 클러스터 1 (104-493) 전체 이동.
  3. `chat/secrets.py` ← 클러스터 2 (`collect_conversation_secret_values`, `_redact_response_tool_calls`). CLAUDE.md 규칙("read/poll은 경량 agent-only 수집 공유")과 정확히 대응되므로 독립 모듈이 규칙 준수 검증에도 유리.
  4. `chat/conversations.py` ← 클러스터 3 (CRUD + 커서). `ConversationPageCursor`, `_encode/_decode_conversation_cursor`, `list_*_page` 이동.
  5. `chat/messages.py` ← 클러스터 4 (`list_messages_from_checkpointer` + deferred import를 top-level로 승격 가능해짐).
  6. `chat/usage.py` ← 클러스터 5, `chat/attachments.py` ← 클러스터 6+7, `chat/runtime_context.py` ← 클러스터 8.
  7. facade는 `from app.services.chat.interrupts import *` 형태 대신 명시적 re-export만.
  8. import 갱신 지점: `grep -rl "from app.services import chat_service\|from app.services.chat_service import"` → 대부분 facade 유지로 무변경. 내부 상호참조(예: 클러스터 8이 클러스터 2를 부름)는 새 모듈 경로로 교체.
- **검증**: `uv run pytest tests/test_chat*.py tests/test_conversations*.py tests/test_triggers.py && uv run ruff check app/services/chat`
- **예상 공수**: L (facade 하위호환 + deferred import 정리 포함)

---

### [BE-S2] MCP/tools/models 도메인에 **서비스 레이어가 아예 없음** — 라우터가 직접 DB 처리
- **우선순위 제안**: P1 — 레이어링 위반의 가장 명백한 케이스. 라우터에 트랜잭션·쿼리·비즈니스 규칙이 섞여 재사용·테스트가 불가.
- **카테고리**: 구조
- **증거**: 라우터 전체 raw DB 접근 224건 중 상위:
  - `routers/mcp.py` (825줄, **28건**): `_load_owned`(92), `_load_tools_for`(103)에 직접 `select`, 핸들러가 `db.add(server)`/`db.commit()`/`db.delete()` 직접 수행(234, 244, 292, 566, 582, 738-740). `import_servers`(455-608)는 credential 존재 확인 `select(Credential.id)`(492)까지 라우터에서 실행.
  - `routers/tools.py` (11건): `create_tool`→`db.add`+`commit`(135-136), `run_tool_endpoint`(251) 실행 로직 인라인.
  - `routers/models.py` (9건): `delete_model`이 `select(func.count(Agent.id))` in-use 체크(254)를 라우터에서 수행.
  - `services/`에 `mcp_service.py`/`model_service.py` 부재 확인. `services/mcp_registry.py`는 static registry 캐시일 뿐 CRUD 아님. 대조적으로 agents/artifacts/memory/triggers/shares는 서비스 존재.
- **문제점**: 트랜잭션 경계가 라우터에 있어 스케줄러·트리거·다른 라우터가 동일 로직을 재사용 불가 → 복붙 발생. ownership 규칙(`_load_owned`)이 라우터마다 재구현되어 enumeration-oracle 방지 규칙(security.md)이 일관 적용되는지 감사 어려움. 단위 테스트가 HTTP 계층을 거쳐야만 가능.
- **리팩토링 방안**:
  1. `app/services/mcp_service.py` 신설. `mcp.py`의 `_load_owned`, `_load_tools_for`, `create_server`/`update_server`/`delete_server`/`import_servers`/`export_servers`의 **DB 조작부만** 함수로 추출(`create_server(db, *, user_id, payload) -> McpServer` 등). 커밋은 서비스 내부 또는 라우터 중 한 곳으로 통일(프로젝트 기존 관례 = 서비스가 `flush`, 라우터가 `commit` → BE-S7과 동일 정책 채택 권장).
  2. 라우터는 서비스 호출 + 스키마 변환 + 권한 가드(`Depends`)만 남긴다. `_invalidate_runtime_mcp_cache`/`_record_mcp_audit` 같은 사이드이펙트도 서비스로.
  3. `tool_service.py`(기존 파일 확장)·`model_service.py`(신설)에 동일 적용. `run_tool_endpoint` 실행 로직은 `tool_service.run_tool(...)`로.
  4. import 갱신: 각 라우터 상단 `from app.services import mcp_service`. 모델 import는 서비스로 이동.
  5. facade 불필요(라우터는 외부 재사용 대상 아님).
- **검증**: `uv run pytest tests/test_mcp*.py tests/test_tools*.py tests/test_models*.py && uv run ruff check app/services/mcp_service.py app/routers/mcp.py`
- **예상 공수**: M (도메인당 반나절 × 3)

---

### [BE-S3] `marketplace/install_service.py` 갓 모듈 — 3개 설치 타입 + update + delete가 1366줄에 혼재
- **우선순위 제안**: P1 — marketplace 최대 파일. skill/mcp/agent_blueprint 3계열의 설치·재설치·바인딩 검증이 뒤엉켜 한 타입 수정이 다른 타입을 깨뜨릴 위험.
- **카테고리**: 구조
- **증거**: `app/marketplace/install_service.py`. 함수군:
  - **공통 유틸/스냅샷**: `_skill_storage_root`, `_target_for`, `_copy_snapshot`, `_rmtree_skill_storage`, `_replace_skill_snapshot` (83-273)
  - **바인딩 검증**: `_validate_version_credential_bindings`, `_validate_mcp_bindings`, `_apply_mcp_payload_to_server`, `_materialize_mcp_tool_snapshot` (380-540)
  - **MCP 설치**: `_install_mcp_item`(541-672), `_mcp_install_status_for_server`, `_overwrite_mcp_installation`(990-1054)
  - **Agent blueprint 설치**: `_install_agent_blueprint_item`(673-810), `_agent_blueprint_status_from_bindings`, `_apply_agent_payload_to_blueprint`, `_overwrite_agent_blueprint_installation`(1055-1153)
  - **오케스트레이터**: `install_item`(811-989, 178줄 — 타입 분기), `update_installation`(1154-1265), `delete_installation`(1266-1306), `_remove_install_artifacts`(1307)
- **문제점**: `install_item`이 3타입을 if/elif 분기하며 각 타입의 상세 로직을 같은 파일에서 호출 → 파일 전체를 알아야 한 타입 수정 가능. MCP 바인딩 검증과 agent blueprint 바인딩 검증이 인접해 복붙·불일치 유발. 스냅샷 복사(파일시스템)와 DB 트랜잭션이 한 함수에 섞여 롤백 정합성 추론 어려움.
- **리팩토링 방안**:
  1. `app/marketplace/install/` 패키지 생성.
  2. `install/snapshot.py` ← 스냅샷/스토리지 유틸(83-273).
  3. `install/bindings.py` ← credential/mcp 바인딩 검증(338-540).
  4. `install/skill.py` / `install/mcp.py` / `install/agent_blueprint.py` ← 타입별 `_install_*`/`_overwrite_*`/`_status_*`.
  5. `install/__init__.py`(또는 기존 `install_service.py` facade)에 `install_item`/`update_installation`/`delete_installation` **디스패처만** 남기고 타입별 모듈로 위임.
  6. 하위호환: `routers/marketplace.py`가 `from app.marketplace import install_service` 사용 → facade에서 3개 public 함수 re-export.
  7. import 갱신: `install_locks.py`, `origin_service.py`가 install 심볼 참조하는지 `grep -rn "install_service\." app/` 확인 후 경로 교체.
- **검증**: `uv run pytest tests/test_marketplace*.py && uv run ruff check app/marketplace/install`
- **예상 공수**: L

---

### [BE-S4] `services ↔ agent_runtime` 양방향 결합 → 순환참조 회피용 함수-로컬 import 24건(chat_service만)
- **우선순위 제안**: P2 — 즉각 버그는 없으나 모듈 경계가 무너져 있어 신규 결합이 계속 늘고, deferred import가 "숨은 런타임 의존"이 되어 import 시점 에러를 실행 시점으로 미룸.
- **카테고리**: 구조
- **증거**: `services/`가 `agent_runtime`을 import하는 파일 **30개**, `agent_runtime/`이 `services`를 import하는 파일 **14개**(양방향). 회피 결과 `chat_service.py`에 함수-로컬 import 24건(예: 464-465, 515-517, 1024, 1057-1059). `scheduler.py`도 전부 함수-로컬 import(117, 228-230, 313, 377, 446, 481, 522-523, 609-610, 638, 687-688)로 부팅 순환을 회피 중.
- **문제점**: 어느 방향이 "상위 레이어"인지 불명확. agent_runtime이 서비스를(DB/CRUD) 부르고 서비스가 다시 agent_runtime을(런타임 조립) 부르는 사이클이 존재해 import graph가 DAG가 아님. deferred import는 정적 분석·IDE 탐색을 무력화하고, 오타/시그니처 변경을 런타임까지 숨긴다.
- **리팩토링 방안**:
  1. 경계 규칙 확정 문서화(`docs/ARCHITECTURE.md`): **agent_runtime = 순수 실행 엔진, services = DB/오케스트레이션**. 방향은 `services → agent_runtime` 단방향만 허용.
  2. `agent_runtime → services` 14개 역방향 import를 조사(`grep -rn "from app.services" app/agent_runtime/`). 대부분 CRUD 조회(memory, followup, agent) → 필요한 데이터를 **호출자(서비스)가 미리 로드해 인자로 주입**하도록 뒤집는다(의존성 역전). 예: `runtime_component_builder._load_memory_context`가 `memory_service`를 직접 부르는 대신, 상위 서비스가 memory records를 조회해 `AgentConfig`에 담아 전달.
  3. 역전 불가한 최소 케이스만 `Protocol` 인터페이스(`app/agent_runtime/ports.py`)로 추상화.
  4. 역전 완료 후 `chat_service`/`scheduler`의 함수-로컬 import를 top-level로 승격, 남는 것만 문서 주석.
- **검증**: `uv run python -c "import app.main"` (부팅 순환 확인) + `uv run pytest` 전체 + `uv run ruff check`(unused import).
- **예상 공수**: L (조사·역전 설계가 핵심)

---

### [BE-S5] `write_tools.py` — `build_write_tools` 단일 함수 1093줄에 26개 툴 클로저
- **우선순위 제안**: P2 — Assistant 패널 도구 전체가 하나의 팩토리 함수 안 nested closure. 한 도구 수정 시 1093줄 스코프를 로드해야 하고 개별 도구 단위 테스트가 사실상 불가.
- **카테고리**: 구조
- **증거**: `agent_runtime/assistant/tools/write_tools.py`. `def build_write_tools(`(53) 하나 안에 `add_tool_to_agent`(72), `remove_tool_from_agent`(107), `add/remove_mcp_tool`(132/170), `add/remove_middleware`(195/226), `add/remove_subagent`(256/328), `add/remove_skill`(367/416), `edit/update_system_prompt`(441/478), `update_model_config`(500), `update_middleware_config`(554), `update_chat_openers`(578), `update_agent_metadata`(601), `update_agent_identity_mode`(635), `update_recursion_limit`(676), cron 5종 `create/update/delete/enable/disable_cron_schedule`(696-980) 등 **26개 async 클로저** + 공유 헬퍼 `_get_agent_with_session`(65), `_resolve_trigger_for_write`(789). 형제 `read_tools.py`(470), `clarify_tools.py`(48)는 이미 분리돼 있음.
- **문제점**: 클로저들이 공유 클로저 변수(session, agent_id 등)에 암묵 의존 → 개별 추출이 어렵게 얽힘. cron 5종(696-980, ~284줄)은 트리거 도메인으로 완전히 독립적인데도 같은 함수에. 파일이 커질수록 신규 도구 추가 시 diff·리뷰 비용 증가.
- **리팩토링 방안**:
  1. 공유 컨텍스트를 `@dataclass WriteToolContext(session_factory, agent_id, ...)`로 명시화(클로저 캡처 → 명시적 객체).
  2. 도구를 그룹별 서브 빌더로 분해: `write_tools/tool_links.py`(add/remove tool·mcp), `write_tools/composition.py`(middleware·subagent·skill), `write_tools/agent_config.py`(prompt·model·metadata·identity·openers·recursion), `write_tools/cron.py`(cron 5종 + `_resolve_trigger_for_write`). 각 빌더가 `ctx`를 받아 `list[BaseTool]` 반환.
  3. `build_write_tools`는 그룹 빌더 결과를 concat하는 얇은 조립자로.
  4. import 갱신: `assistant_agent.py`가 `build_write_tools`만 참조하므로 시그니처 유지 시 무변경.
- **검증**: `uv run pytest tests/test_assistant*.py && uv run ruff check app/agent_runtime/assistant/tools`
- **예상 공수**: M

---

### [BE-S6] 디렉토리 컨벤션 이원화 — 도메인 패키지(`app/marketplace/`,`mcp/`,`credentials/`,`skills/`) vs 평면 `services/*_service.py`
- **우선순위 제안**: P2 — 신규 도메인을 어디에 둘지 규칙이 없어 팀마다 다르게 배치. "marketplace 로직은 어디?"를 매번 탐색해야 함.
- **카테고리**: 구조
- **증거**: 비즈니스 로직이 두 위치에 공존 — (a) 도메인 패키지: `app/marketplace/`(19파일), `app/mcp/`(7), `app/credentials/`(11), `app/skills/`(14), `app/agent_api/`(6). (b) 평면 서비스: `app/services/`에 90+ 파일(`agent_service.py`, `artifact_service.py`, `conversation_*` 등). `services/marketplace_service.py`는 없음(marketplace는 패키지). 반면 conversation/agent CRUD는 평면 서비스. 같은 "서비스 레이어"인데 물리 위치 규칙이 다름.
- **문제점**: 어떤 도메인은 자기 패키지(내부에 service/schemas/payloads 응집), 어떤 도메인은 `services/`에 흩어짐(`conversation_*` 12개 파일이 `services/` 루트에 평면 나열). 신규 개발자가 도메인 코드를 찾는 비용 증가, 리팩토링 시 "이 도메인은 어느 컨벤션?" 판단 필요.
- **리팩토링 방안**:
  1. **결정 기록**(ADR 신설, 예: ADR-020 "Service layout convention"): 규모 임계값 정의 — "파일 3개 이상 = 도메인 패키지 `app/<domain>/`, 그 미만 = `services/<domain>_service.py`".
  2. 즉시 이관은 위험하므로 **신규 규칙 + 점진 이관**. 우선 `services/`의 응집 그룹(`conversation_*` 12개, `skill_evaluation_*` 20+개, `skill_builder_*` 8개, `skill_revision_*` 5개, `model_*` 6개)을 각각 `services/conversation/`, `services/skill_evaluation/`, `services/skill_builder/` 서브패키지로 묶는다(순수 이동 + facade).
  3. facade 필요: 이동한 모듈은 `services/__init__` 또는 얇은 re-export로 기존 import 경로 보존 후, 점진적으로 호출부 갱신.
- **검증**: `uv run pytest`(전체) — 순수 이동이므로 그린 유지가 검증. `uv run ruff check app/services`.
- **예상 공수**: M (규칙 결정 S + 서브패키징 M)

---

### [BE-S7] `credentials.py` 라우터가 OAuth2 플로우 비즈니스 로직 ~286줄을 직접 보유
- **우선순위 제안**: P1 — 시크릿/토큰 교환이라 보안 민감 경로인데 라우터에 오케스트레이션이 있어 재사용·테스트·감사가 어렵다. credentials는 이미 `app/credentials/service.py`가 있어 "부분 서비스"인데 OAuth만 라우터에 남음.
- **카테고리**: 구조
- **증거**: `routers/credentials.py`(786줄). CRUD는 `credential_service.create/update`에 위임(156-180 확인)하나 **커밋을 라우터에서**(`await db.commit()` 176). OAuth 계열은 전부 라우터: `_prepare_mcp_oauth_data`(491), `_persist_credential_payload`(574), `_gc_oauth_states`(584, `db.execute` 직접), `oauth2_auth_start`(617, `db.add(CredentialOAuthState)` 667), `oauth2_callback`(708, `select(CredentialOAuthState)` 718 + `select(Credential)` 732 + 토큰 교환 + `db.commit` 777). 별도로 `credentials/mcp_oauth_client.py`(474줄)가 존재하는데 라우터가 그 위 오케스트레이션을 중복 보유.
- **문제점**: OAuth state 생성/해시/저장/콜백/토큰 교환/GC가 HTTP 핸들러에 인라인 → MCP 서버 등록 플로우(mcp.py)나 스케줄러의 state GC가 동일 로직을 재사용 불가. 보안 리뷰가 라우터 코드를 훑어야 함. `_gc_oauth_states`는 스케줄러 GC 잡과 개념 중복.
- **리팩토링 방안**:
  1. `app/credentials/oauth_service.py` 신설. `_prepare_mcp_oauth_data`, `_persist_credential_payload`, `_gc_oauth_states`, `oauth2_auth_start`/`oauth2_callback`의 **DB·토큰 교환 로직** 이동. 함수 시그니처는 `start_oauth(db, *, user, credential_id) -> AuthStartResult`, `handle_callback(db, *, code, state) -> Credential`.
  2. `mcp_oauth_client.py`와 역할 정리: client=저수준 HTTP, oauth_service=DB 상태·오케스트레이션.
  3. 라우터는 서비스 호출 + 리다이렉트/응답 변환만.
  4. 트랜잭션 정책 통일: 서비스가 `flush`, 라우터가 `commit`(현 create 패턴과 일치) 또는 그 반대로 프로젝트 전역 결정(BE-S2와 함께).
  5. `_gc_oauth_states`는 스케줄러가 재사용하도록 서비스 함수로 노출.
- **검증**: `uv run pytest tests/test_credentials*.py tests/test_*oauth*.py && uv run ruff check app/credentials/oauth_service.py`
- **예상 공수**: M

---

### [BE-S8] `artifact_service.py`(1037줄) — 델타 레코더/ingest + CRUD + 라이브러리 쿼리 + 스토리지 헬퍼 혼재
- **우선순위 제안**: P2 — 스트리밍 중 파일 감지(hot path)와 라이브러리 조회(read path)가 한 모듈이라 성능 특성이 다른 코드가 결합.
- **카테고리**: 구조
- **증거**: `services/artifact_service.py`. 클러스터:
  - **스냅샷/델타/ingest**: `ArtifactFileState`~`ArtifactDeltaRecorder`(45-151), `snapshot_output_dir`(152), `diff_snapshots`(211), `ingest_changed_files`(230-364)
  - **런당 마감/링크**: `link_artifacts_to_messages`(392), `finalize_artifacts_for_run`(413)
  - **라이브러리/조회 CRUD**: `list_conversation_artifacts`(365), `list_library_artifacts`(503), `list_recent_artifacts`(574), `set_artifact_favorite`(601), `record_artifact_opened/download`(614/627), `get_library_stats`(639), `delete_artifact`(774)
  - **콘텐츠/스토리지 헬퍼**: `read_artifact_text_content`(697), `get_artifact_download_path`(729), `_storage_local_path`(834), `_sha256_file*`(1016-1027), `_summaries_from_artifacts`(841), `_file_event_payload`(1028)
- **문제점**: 델타 레코딩(runtime 스트리밍이 매 파일 이벤트마다 호출)과 라이브러리 페이지네이션(UI 폴링)이 같은 파일 → 한쪽 변경 시 다른 쪽 회귀 위험, import 표면이 넓음.
- **리팩토링 방안**:
  1. `app/services/artifacts/` 패키지화, `artifact_service.py`는 facade.
  2. `artifacts/recorder.py` ← `ArtifactFileState`/`ArtifactSnapshot`/`ArtifactDelta`/`ArtifactDeltaRecorder`/`snapshot_output_dir`/`diff_snapshots`/`ingest_changed_files`/`finalize_artifacts_for_run`.
  3. `artifacts/library.py` ← 조회/즐겨찾기/stats/커서(`_encode/_decode_library_cursor` 포함).
  4. `artifacts/content.py` ← 텍스트 읽기·다운로드·sha256·storage_local_path.
  5. `artifacts/summary.py` ← `_summaries_from_artifacts`/`_summary_from_*`/`_file_event_payload`.
  6. facade에서 기존 public 심볼 re-export(streaming.py·routers/artifacts.py 의존 보존; `grep -rn "artifact_service\." app/`로 지점 확인).
- **검증**: `uv run pytest tests/test_artifact*.py && uv run ruff check app/services/artifacts`
- **예상 공수**: M

---

### [BE-S9] `scheduler.py`(752줄) — 11개 잡의 비즈니스 로직이 등록 코드와 한 모듈에 인라인
- **우선순위 제안**: P2 — 스케줄러는 "무엇을 언제 돌릴지"만 알아야 하는데 "어떻게"까지 들고 있어, 잡 로직 변경이 스케줄러 부팅과 얽힘. 전부 함수-로컬 import로 순환 회피 중(BE-S4의 증상).
- **증거**: `app/scheduler.py`. `_run` 본문에 실제 비즈니스 로직 인라인: `rotate_credentials_to_active_key`(218-253, credential 회전 알고리즘), `poll_mcp_servers_health`(511-571, MCP health 폴링), `sweep_stale_conversation_runs`(607-631), `cleanup_skill_runtime_roots`(679), `draft_conversation_gc_run`(436)/`orphan_attachment_gc_run`(472)는 `chat_service`로 위임(446, 481)하나 나머지는 인라인. 각 잡마다 `register_*_job()` 짝(11쌍). 모든 의존이 함수-로컬 import.
- **문제점**: 잡 로직 단위 테스트가 스케줄러 모듈을 거쳐야 함. credential 회전 같은 알고리즘이 스케줄러에 있어 수동 실행·재사용 불가(MEMORY 감사에서 `rotate_credentials 무한루프` 지적된 로직이 여기 인라인). 부팅 시 순환 때문에 전 함수가 지연 import.
- **리팩토링 방안**:
  1. 잡 **본문(로직)**을 각 도메인 서비스로 이동: `rotate_credentials_to_active_key` → `credentials/service.py`(또는 신설 `credential_rotation.py`), `poll_mcp_servers_health` → `services/mcp_service.py`(BE-S2와 연계), `sweep_stale_conversation_runs` → `conversation_run_service.py`, `cleanup_skill_runtime_roots` → `marketplace/skill_runtime.py`.
  2. `scheduler.py`는 **등록만** 남긴다: `register_*_job()`이 서비스 함수를 `scheduler.add_job(target=service.fn, ...)`로 참조. `_job_id`/`_naive_utc`/`get_scheduler`/leader-election(27-92)은 순수 인프라라 유지.
  3. 로직이 서비스로 가면 top-level import 가능해져 함수-로컬 import 제거(BE-S4 부분 해소).
  4. facade 불필요.
- **검증**: `uv run pytest tests/test_scheduler*.py tests/test_credentials*.py tests/test_mcp*.py && uv run python -c "import app.scheduler"`
- **예상 공수**: M

---

### [BE-S10] `runtime_component_builder.py`(828줄) — 모델 조립 + 미들웨어 + 메모리 + 프롬프트 + 인터럽트 정책 다중 관심사
- **우선순위 제안**: P2
- **증거**: `agent_runtime/runtime_component_builder.py`:
  - **모델 후보/폴백**: `_resolve_middleware_model_params`(103), `_model_constructor_params`(133), `_model_chain`(154), `_build_model_candidates`(166), `_build_model_with_fallback`(219), `_is_retryable_model_error`(225)
  - **신뢰성 미들웨어**: `_has_visible_ai_content`(233), `EmptyContentRetryMiddleware`(254), `_build_default_reliability_middleware`(279)
  - **인터럽트 정책**: `_default_interrupt_on_from_tools`(354), `_build_interrupt_on_policy`(363)
  - **메모리**: `_recalled_memory_briefs`(448), `_load_memory_context`(464, `memory_service` 역방향 import), `_memory_write_policy_for_run`(500)
  - **프롬프트 빌더**: `_system_prompt_with_temporal_context`(428), `_memory_tool_instruction_prompt`(521), `_interactive_tool_instruction_prompt`(541), `_artifact_file_instruction_prompt`(557)
  - **오케스트레이터**: `_prepare_runtime_components`(571), `_prepare_agent`(752), `build_agent`(66)
- **문제점**: 모델 폴백 로직·미들웨어 정의·프롬프트 문자열·메모리 조회가 한 파일이라 각기 다른 변경 이유(모델 제공자 quirk vs 프롬프트 카피 vs 메모리 정책)가 충돌. `EmptyContentRetryMiddleware` 클래스가 조립자 안에 정의됨.
- **리팩토링 방안**:
  1. `agent_runtime/runtime/models.py` ← 모델 후보/폴백/재시도 판정(103-231).
  2. `agent_runtime/runtime/reliability.py` ← `EmptyContentRetryMiddleware` + `_build_default_reliability_middleware`.
  3. `agent_runtime/runtime/interrupts.py` ← 인터럽트 정책(354-393). (CLAUDE.md의 `_default_interrupt_on_from_tools` 규칙과 대응 → 독립 모듈이 규칙 추적에 유리.)
  4. `agent_runtime/runtime/prompts.py` ← 4개 프롬프트 빌더(428, 521-570).
  5. `agent_runtime/runtime/memory_context.py` ← 메모리 3함수(BE-S4의 역전 대상 — 여기서 `memory_service` 직접 호출을 인자 주입으로).
  6. `runtime_component_builder.py`는 `_prepare_runtime_components`/`_prepare_agent`/`build_agent` 조립자만 유지 + 위 모듈 import.
- **검증**: `uv run pytest tests/test_runtime*.py tests/test_agent_stream*.py tests/test_memory*.py && uv run ruff check app/agent_runtime/runtime`
- **예상 공수**: M

---

### [BE-S11] `marketplace/` 프로젝션 로직 분산 — `service.py` + `origin_service.py`가 설치/발행 상태 계산을 중복 소유
- **우선순위 제안**: P3
- **증거**: `marketplace/service.py`의 `_project_item`(261)/`_project_items`(383)/`_publication_state_for_owner`(404)와 `marketplace/origin_service.py`의 `derive_origin_summary_for_skill`(70)/`_derive_publication_state`(128)/`derive_publication_summary_for_skill`(156)/`bulk_derive_publication_summaries`(198)/`derive_installation_summary`(265)/`bulk_derive_installation_summaries`(587)가 모두 "카탈로그 아이템의 발행·설치 상태"를 계산. `_publication_state_for_owner`(service.py)와 `_derive_publication_state`(origin_service.py)는 이름부터 관심사 중복.
- **문제점**: 발행 상태 규칙이 바뀌면 두 파일을 동시 수정해야 하고 누락 시 catalog list와 detail이 불일치. `origin_service.py`(786줄)는 사실상 "프로젝션 서비스"인데 `service.py`도 프로젝션을 함.
- **리팩토링 방안**:
  1. 프로젝션 계산을 단일 모듈 `marketplace/projection.py`로 수렴: `_publication_state_for_owner`와 `_derive_publication_state`를 하나로 통합(호출부가 owner 관점/뷰어 관점을 파라미터로).
  2. `service.py`는 카탈로그 **쿼리/페이지네이션**(`_base_catalog_query`, `list_items_page` 등)만, `projection.py`는 상태 파생만, `origin_service.py`는 installation 요약만으로 책임 정리.
  3. 통합 전 두 상태 계산이 실제로 동일 결과인지 테스트로 고정(characterization test) 후 병합.
- **검증**: `uv run pytest tests/test_marketplace*.py -k "project or publication or installation" && uv run ruff check app/marketplace`
- **예상 공수**: M

---

### [BE-S12] `config.py`(262줄) / `dependencies.py`(182줄) 비대화 점검 — **현행 유지 권장(리팩토링 불필요)**
- **우선순위 제안**: P3 — 임계 미만. 과잉 분리 금지 원칙(Simplicity First)에 따라 지금은 두지 말 것을 명시적으로 권고.
- **증거**: `app/config.py` — 단일 `Settings(BaseSettings)`에 필드 101개지만 섹션 주석으로 잘 구획됨(6-262). `app/dependencies.py` 182줄 — DI만, 비대 아님.
- **리팩토링 방안**: **하지 말 것.** 지금은 no-op.
- **예상 공수**: S (판단만)

---

### 요약 — 우선순위별 정리

| ID | 제목 | 우선순위 | 공수 |
|----|------|:---:|:---:|
| BE-S1 | chat_service.py 갓 모듈 8-클러스터 분해 | **P1** | L |
| BE-S2 | MCP/tools/models 서비스 레이어 부재(라우터 raw DB) | **P1** | M |
| BE-S3 | install_service.py 3-타입 갓 모듈 분해 | **P1** | L |
| BE-S7 | credentials 라우터 OAuth 로직 → oauth_service | **P1** | M |
| BE-S4 | services↔agent_runtime 양방향 결합 역전 | P2 | L |
| BE-S5 | write_tools.py 1093줄 단일 팩토리 분해 | P2 | M |
| BE-S6 | 디렉토리 컨벤션 이원화(ADR + 점진 서브패키징) | P2 | M |
| BE-S8 | artifact_service.py recorder/library/content 분해 | P2 | M |
| BE-S9 | scheduler.py 잡 로직 → 도메인 서비스 이관 | P2 | M |
| BE-S10 | runtime_component_builder.py 5-관심사 분해 | P2 | M |
| BE-S11 | marketplace 프로젝션 중복(service↔origin_service) 통합 | P3 | M |
| BE-S12 | config/dependencies — **현행 유지(하지 말 것)** | P3 | S |

**착수 순서 권고**: BE-S2·BE-S7(레이어링 위반 = 명확한 정답, blast radius 작음) → BE-S1·BE-S3(최대 god module, facade로 안전) → BE-S4·BE-S9(순환/스케줄러는 함께 풀면 시너지) → 나머지 P2 → BE-S6는 ADR 결정 후 점진.

**핵심 인사이트**: 이 코드베이스는 분해 역량이 있다(`conversation_agent_protocol*` 18파일, executor split, marketplace 세분화가 증거). 문제는 **일관성** — 같은 패턴이 chat/artifact/scheduler/write_tools/install에는 아직 적용 안 됨. 대부분 facade 기반 순수 이동이라 리스크 낮음. 유일한 설계 난제는 BE-S4(services↔agent_runtime 방향 역전)이며 이것이 여러 god module의 함수-로컬 import 냄새의 근본 원인이다.

---

## 5. 백엔드 — 성능 (BE-P)

**범위**: `backend/app` 읽기 전용 분석. 7개 병렬 조사(N+1, 블로킹, 인덱스, 스트리밍, 스케줄러, 풀/페이지네이션) + 핵심 파일 직접 검증.
**우선순위 기준**: 사용자 체감 빈도 × 비용. **P1 = 활성 채팅당 반복 폴링/스트리밍 hot path 또는 전역 이벤트 루프 블로킹** / P2 = 데이터 증가에 따라 무한정 악화되거나 쓰기/설치 경로 / P3 = 관리·백그라운드 경로.

---

### [BE-P1] `GET /messages` 폴링마다 pending-interrupt 하이드레이션이 MessageEvent 행당 별도 쿼리 (N+1)
- **우선순위 제안**: **P1** — `GET /messages`는 활성 대화당 반복 폴링되는 최상위 hot path. 대화가 길수록 폴링당 쿼리 수가 선형 증가.
- **카테고리**: 성능 (N+1)
- **증거**: `services/chat_service.py:468-491` (루프) + `services/trace_storage.py:163-171` (행당 쿼리)
  ```python
  # chat_service.py:473-478
  for record in result.scalars().all():
      target_responses = [response_by_id[mid] for mid in linked_ids if mid in response_by_id]
      if not target_responses:
          continue
      events = await trace_storage.load_events(db, record)   # ← 루프 안 await DB
  ```
  `load_events`는 `record`마다 `select(MessageEventChunk.events).where(message_event_id == record.id)` 1건씩 실행. `_messages_to_response`(`chat_service.py:1136`, `user_id` 있을 때) 경유로 **모든 인증 `GET /messages`에서 실행**.
- **문제점**: 툴콜 40턴 대화 = 1(부모) + 최대 40(chunk) = 41 쿼리/폴링. 대화 길이에 선형 증가하며 채팅 UI가 폴링할 때마다 반복.
- **리팩토링 방안**:
  1. 루프 전에 `target_responses`가 비지 않은 `record`만 필터링해 id 수집.
  2. `select(MessageEventChunk.message_event_id, MessageEventChunk.events).where(message_event_id.in_(ids)).order_by(message_event_id, seq_start, created_at)` 단일 IN 쿼리로 배치.
  3. `dict[event_id, list[events]]`로 그룹핑 후 in-memory 병합. → N+1 을 2 쿼리로.
- **검증**: SQLAlchemy `echo=True` 또는 pytest에서 쿼리 카운터 픽스처로 40턴 대화 폴링 시 쿼리 수 41→2 확인.
- **예상 공수**: **M**

---

### [BE-P2] `GET /messages`가 대화 전체를 무제한 로드 (페이지네이션·상한 부재)
- **우선순위 제안**: **P1** — 위와 같은 폴링 hot path. checkpointer 풀(BE-P7)과 직접 복합 악화.
- **카테고리**: 성능 (페이지네이션)
- **증거**: `routers/conversation_messages.py:137` → `services/chat_service.py:1031, 1079`
  ```python
  # chat_service.py:1079
  messages = [node.message for node in tree.nodes]   # limit/cursor 없음
  ```
  `build_message_tree`가 매 호출 전체 checkpoint 트리를 순회하고 모든 메시지를 직렬화.
- **문제점**: 수백~수천 턴/브랜치 대화는 폴링마다 전체 트리 재순회 + 전량 직렬화. checkpointer 풀 커넥션도 폴링마다 점유(BE-P7 복합).
- **리팩토링 방안**:
  1. 최근 N개 메시지 + 역방향 keyset 커서(메시지 index 기준) 반환. 오래된 턴은 lazy-load.
  2. 최소한 하드 max limit 강제.
  3. 폴링 경로에서 전체 checkpoint 트리 재순회 회피(증분/캐시).
- **검증**: 500턴 대화 fixture로 `GET /messages` 응답 크기·지연 측정, 상한 적용 전후 비교.
- **예상 공수**: **L** (checkpoint 트리 순회 구조 변경 수반)

---

### [BE-P3] 폴링 경로의 `build_tools_config`가 MCP 툴 링크당 `SELECT … FOR UPDATE` 반복 (N+1 + 불필요한 락)
- **우선순위 제안**: **P1** — `collect_conversation_secret_values`(`chat_service.py:568`)가 **모든 `GET /messages`·`GET /threads/{id}/state` 폴링**에서 실행. 읽기 경로에서 행 락까지 취함.
- **카테고리**: 성능 (N+1 + 락 경합)
- **증거**: `services/chat_service.py:1702-1734` (루프) + `mcp/auth.py:61-64`
  ```python
  # chat_service.py:1719
  resolved_auth = await resolve_mcp_auth(db, credential_id=server.credential_id, ...)
  # mcp/auth.py:64
  credential = (await db.execute(stmt.with_for_update())).scalar_one_or_none()
  ```
  게다가 이 credential 행은 이미 `_agent_runtime_load_options`(`chat_service.py:1521-1524`, `selectinload(McpTool.server).selectinload(McpServer.credential)`)로 eager load 되어 있어 **재조회가 중복**.
- **문제점**: credential 있는 MCP 툴 M개면 폴링당 M회 `SELECT … FOR UPDATE`. (1) MCP 툴 수에 선형, (2) 순수 읽기 경로에서 행 락 → 동시 스트림과 락 경합.
- **리팩토링 방안**:
  1. `collect_conversation_secret_values`(읽기 경로)는 `build_tools_config`에 `db=None` 전달 → 이미 eager-load된 `server.credential`을 `decrypt_cached`로 in-memory 복호(라인 1733 분기가 이미 존재).
  2. run 경로가 `resolve_mcp_auth`를 유지해야 하면, 읽기 caller에는 `.with_for_update()` 제거 + `WHERE id IN (...)` 배치.
- **검증**: 폴링 시 `pg_locks` 관찰 + 쿼리 카운터로 M+1→0(읽기 경로) 확인.
- **예상 공수**: **S~M**

---

### [BE-P4] bcrypt 해시/검증이 이벤트 루프를 통째로 블로킹 (로그인·회원가입마다 ~250ms)
- **우선순위 제안**: **P1** — 단일 로그인이 **전체 서버**의 모든 동시 SSE 스트림·폴링·API를 ~250ms 정지시킴. credential-stuffing 시 서버 직렬화.
- **카테고리**: 성능 (이벤트 루프 블로킹)
- **증거**: `services/auth_service.py:113, 131`(+`:84`), `auth/password.py:28`
  ```python
  # auth_service.py:131  (async def authenticate)
  if not verify_password(password, user.hashed_password):   # ~250ms 동기
  # auth_service.py:113  존재하지 않는 이메일도 타이밍 패드로 동일 비용
  verify_password(password, _DUMMY_PASSWORD_HASH)
  # password.py:28  bcrypt__rounds=12  (의도적 느린 KDF)
  ```
- **문제점**: bcrypt cost-12 ≈ 250ms를 루프 스레드에서 실행 → 그동안 어떤 코루틴도 스케줄 불가. 실패/가짜 로그인도 패드로 250ms 소모.
- **리팩토링 방안**: `await asyncio.to_thread(verify_password, …)` / `await asyncio.to_thread(hash_password, …)`로 워커 스레드에 오프로드. `_DUMMY_PASSWORD_HASH`(`:43`)는 import 타임이라 무관.
- **검증**: 부하 도구로 로그인 20 rps 동시 + 별도 `GET /health` 지연 측정, to_thread 전후 p99 비교. 기존 auth 테스트 회귀 확인.
- **예상 공수**: **S**

---

### [BE-P5] SSE 스트리밍 이벤트당 중복 비용 (redaction 2회 + 버려지는 json.dumps + 시크릿셋 재정렬 + O(n²) 이벤트 id 재로드)
- **우선순위 제안**: **P1** — 토큰당(응답당 10²~10³회) 곱해지는 hot path. 여러 소항목의 복합.
- **카테고리**: 성능 (per-event CPU + 준-quadratic DB)
- **증거**:
  - **(a) 이벤트당 버려지는 `json.dumps`** — `agent_runtime/protocol_events.py:50-55`, 무조건 호출(`:104`). 직렬화 가능성 검증용으로 stdlib `json.dumps` 전체 인코드 후 결과 폐기. `values` 이벤트는 전체 그래프 상태를 매번 재인코드.
  - **(b) redaction 2회** — `langgraph_streaming.py:338-354`(wire) + `protocol_persistence.py:15-24`(persist). `redact_protocol_data`가 이벤트당 전체 재귀를 **두 번**(`_mask_known_values` + `_redact_sensitive_keys`).
  - **(c) 시크릿셋 매 문자열 노드마다 재구축** — `marketplace/redaction.py:70-80`. run 내 불변인 시크릿셋을 문자열 키/값마다 `set` 재생성 + `sorted(key=len)`.
  - **(d) O(n²) 이벤트 id 재로드** — `services/trace_storage.py:147-160`. 부분 플러시(32건/2s)마다 **누적된 모든** chunk `event_ids`를 재 SELECT + set 구축. T 이벤트 턴에서 ≈ O(T²/64).
  - **참고 (양호)**: 모든 redaction 정규식은 모듈 레벨 `re.compile`, 값 마스킹은 `str.replace`(ReDoS 무관), DB 영속화는 32건/2s 배치. 컴파일-인-루프는 없음.
- **문제점**: 2000 토큰 응답 = json.dumps 2000회(+resequence 시 2배, `values`는 전체상태), redaction 2×2000 재귀, 시크릿 정렬 ~16k회, 이벤트 id 재로드 ~62k 누적. 딥리서치/멀티툴 긴 턴에서 CPU·DB 급증.
- **리팩토링 방안**:
  1. (a) `_jsonable`의 검증용 `json.dumps` 제거(payload는 이미 `_serialize_value`로 정규화됨) 또는 debug 플래그 게이트.
  2. (b) wire에서 1회 redaction 후, persist는 `values`/`updates`만 compact 추가 — 재-redaction 제거.
  3. (c) run당 1회 `sorted(unique, key=len)` 메모이즈(ContextVar set 아이덴티티 키) 후 `replace_secret_values`에 주입.
  4. (d) persist 클로저(`conversation_stream_service.py:349`)에 run-scoped `seen_event_ids` set 유지, DB 재로드는 retry 경로에만.
  5. (추가) v3 플러시가 `emit`에서 inline `await`(`langgraph_streaming.py:354`)라 토큰 방출을 블로킹 → legacy처럼 `asyncio.create_task` fire-and-forget(`streaming.py:338`) 패턴으로.
- **검증**: 2000토큰 스크립트 모델 런에서 이벤트당 CPU 프로파일(py-spy) + 긴 턴 총 쿼리 수 측정, 개선 전후 비교.
- **예상 공수**: **M** (소항목 독립 적용 가능, (d)는 별도)

---

### [BE-P6] FK·필터 컬럼 인덱스 누락 (5건, 전부 새 Alembic 마이그레이션 필요)
- **우선순위 제안**: **P2** — 특히 `token_usages`는 스키마에서 가장 빠르게 증가(LLM 턴마다 1행)하는데 **비-PK 인덱스가 0개**.
- **카테고리**: 성능 (인덱스)
- **증거** (66개 마이그레이션 전수 대조로 실제 미존재 확인):
  1. `models/token_usage.py:20` `agent_id` FK — `usage_service.py:18-23`의 `SUM(...) WHERE agent_id = ?`(`GET /api/agents/{id}/usage`)가 매번 풀스캔. **인덱스 전무 직접 확인** (PK만 존재).
  2. `models/token_usage.py:17` `conversation_id` FK(`ON DELETE CASCADE`) — 대화 삭제 시 캐스케이드가 전체 테이블 스캔.
  3. `models/message_attachment.py:28` `conversation_id` FK — `chat_service.py:1176-1178`(`GET /messages` 폴링) + `/files`(`:1450`)에서 필터. 유일 인덱스는 `message_id`(m28).
  4. `models/mcp_server.py:44` `user_id` FK — MCP 서버 목록(`routers/mcp.py:200`) 등 다수가 `WHERE user_id = ?`. 인덱스 전무.
  5. `models/agent_trigger.py:16` `agent_id` FK — `trigger_service.py:203`, `agent_service.py:544`. 기존 복합(`(user_id,status)` m47, `(status,next_run_at)` m53)이 `agent_id` 선두가 아니라 미커버.
- **리팩토링 방안**: 새 Alembic 마이그레이션에서 `Index("ix_token_usages_agent_id","agent_id")`, `ix_token_usages_conversation_id`, `ix_message_attachments_conversation_id`(부분 인덱스 `WHERE message_id IS NOT NULL` 고려), `ix_mcp_servers_user_id`, `ix_agent_triggers_agent_id`(또는 `(agent_id,status)` 복합) 생성. 모델은 이미 프로덕션 존재라 `index=True`만으론 불가 → 마이그레이션 필수.
- **검증**: `EXPLAIN ANALYZE`로 인덱스 스캔 전환 확인 + 대량 행 시드 후 지연 측정.
- **예상 공수**: **S** (마이그레이션 1개)

---

### [BE-P7] checkpointer 풀 `max_size=10` 전역 병목 + 메인 엔진 풀 라이브러리 기본값
- **우선순위 제안**: **P2** — CLAUDE.md에 이미 증상 문서화("슬로우 스트리밍/평가 런 다수 동시 → 백엔드 직렬화, 무관한 요청 timeout").
- **카테고리**: 성능 (커넥션 풀)
- **증거**:
  - `agent_runtime/checkpointer.py:47-53` — `AsyncConnectionPool(min_size=1, max_size=10)`. 모든 스트림 쓰기 + **읽기 폴링**이 이 10 커넥션을 공유·경합.
  - `database.py:15` — `create_async_engine(url, echo=False, pool_pre_ping=True)`. `pool_size`/`max_overflow`/`pool_timeout`/`pool_recycle` 전부 미설정 → 기본 5+10=15, `pool_recycle=-1`. config에 엔진 풀 노브 없음.
  - httpx: tool client 싱글턴(`tool_factory.py:97`) + model client 캐시(`model_factory.py`)는 **양호**.
- **리팩토링 방안**:
  1. `CHECKPOINTER_POOL_MAX_SIZE` 상향(예 20~30, PG `max_connections`에서 엔진 몫 제외), `min_size` 2~4로 warm 유지.
  2. 엔진 `pool_size`/`max_overflow`/`pool_recycle`(예 1800s)을 settings로 노출·설정.
  3. BE-P2로 읽기 경로의 풀 압력 자체를 감소.
- **검증**: 동시 스트림 N개 + 폴링 부하에서 `pg_stat_activity`·풀 대기 관측, 상향 전후 timeout율 비교.
- **예상 공수**: **S** (설정) ~ M (엔진 노브 노출)

---

### [BE-P8] `health_check_history` 무한 증가 — retention 설정이 dead code
- **우선순위 제안**: **P2**
- **카테고리**: 성능 (테이블 팽창)
- **증거**: `services/health_check.py:222-225, 296` (daily cron `0 4 * * *`) — 모델·서버당 1 INSERT/매일. `config.py:88` `health_check_history_retention_days: int = 90` — **어디서도 참조 안 됨**, `delete(HealthCheckHistory)` 코드 전무.
- **리팩토링 방안**:
  1. GC 잡 추가: `DELETE FROM health_check_history WHERE checked_at < now() - retention_days`(설정 이미 존재).
  2. `checked_at` 인덱스 추가.
  3. disabled/미사용 모델은 매일 프로브 제외.
- **검증**: 대량 시드 후 GC 잡 실행으로 행 감소 확인.
- **예상 공수**: **S**

---

### [BE-P9] `mcp_health_poll`이 5분마다 모든 활성 MCP 서버를 직렬 재프로브
- **우선순위 제안**: **P2**
- **카테고리**: 성능 (스케줄러 잡)
- **증거**: `scheduler.py:511, 532, 544` (`IntervalTrigger(minutes=5)`) — `select(McpServer).where(or_(is_system.is_(True), status != "disabled"))` LIMIT 없음, `for server in rows:` 순차 — 서버당 credential 복호 + connect_and_list 라이브 라운드트립.
- **문제점**: `O(#서버)` 네트워크 커넥트를 5분마다 직렬. error/unreachable 서버도 backoff 없이 반복.
- **리팩토링 방안**:
  1. 바운디드 `asyncio.gather`/세마포어로 병렬화(`check_all_active`가 이미 하는 패턴).
  2. per-probe 타임아웃 예산.
  3. `health_polled_at` stale 서버만 증분 폴링 + 지속 실패 서버 지수 backoff.
- **검증**: 서버 50개 시드 후 sweep wall-clock 병렬화 전후 측정.
- **예상 공수**: **M**

---

### [BE-P10] 마켓플레이스 MCP 설치 시 툴당 별도 SELECT — 배치 쿼리가 3줄 아래 이미 존재
- **우선순위 제안**: **P2**
- **카테고리**: 성능 (N+1)
- **증거**: `marketplace/install_service.py:489-524` — `:498` 루프 안 툴당 `select(McpTool)...limit(1)`, `:522` 루프 직후 정확히 필요한 배치 쿼리가 이미 실행됨.
- **리팩토링 방안**: `:522` 쿼리를 루프 전으로 hoist → `existing_by_name = {t.name: t for t in existing_tools}` → 루프 내 `existing_by_name.get(name)`. `:532` stale 링크 삭제도 `delete(...).where(mcp_tool_id.in_(stale_ids))` 단일문으로.
- **검증**: 30툴 서버 설치 시 쿼리 카운트 확인.
- **예상 공수**: **S**

---

### [BE-P11] 런 파일 인제스트 `ingest_changed_files`가 변경 파일당 다중 SELECT
- **우선순위 제안**: **P2**
- **카테고리**: 성능 (N+1)
- **증거**: `services/artifact_service.py:239-298` — `:245` 존재 확인, `:274` 재확인, `:291` max(version_number) — 델타(파일)당. `_summary_from_artifact`(`:269/:359`)가 추가 `_current_version` SELECT.
- **리팩토링 방안**:
  1. 루프 전 `select(ConversationArtifact).where(conversation_id, assistant_msg_id, logical_path.in_([...]))` → `{logical_path: artifact}`.
  2. `select(ArtifactVersion.artifact_id, func.max(version_number)).where(artifact_id.in_(...)).group_by(...)` 배치.
  3. 루프는 in-memory 조회 + insert만.
- **검증**: 파일 10개 변경 런에서 쿼리 수 측정.
- **예상 공수**: **M**

---

### [BE-P12] 무제한 목록: `GET /api/memories` 상한 없음 + 마켓플레이스 OFFSET 페이지네이션
- **우선순위 제안**: **P2**
- **카테고리**: 성능 (페이지네이션)
- **증거**:
  - `services/memory_service.py:368-373` — limit/커서 없음(라우터 `routers/memory.py:81-96`에 `limit` 파라미터 자체가 없음). 대조로 `list_runtime_memory_records`(`:376`)는 `.limit(RUNTIME_MEMORY_MAX_RECORDS)`로 이미 상한.
  - `marketplace/service.py:207, 460-507` — `.limit(limit).offset(offset)` + post-filter가 페이지 채울 때까지 `raw_offset += batch_size` 재조회 루프.
- **리팩토링 방안**:
  1. memories: `limit`(서버 강제 max) + `(updated_at, id)` keyset — 대화 목록 패턴 재사용.
  2. 마켓: `(created_at, id)` keyset 전환 + post-load 필터를 가능한 SQL로 push down.
- **검증**: 대량 memory/마켓 아이템 시드 후 깊은 페이지 지연 비교.
- **예상 공수**: **M**

---

### [BE-P13] async 핸들러 내 동기 CPU/파일 작업 (web_scraper HTML 파싱, 스킬 zip export)
- **우선순위 제안**: **P3**
- **카테고리**: 성능 (이벤트 루프 블로킹)
- **증거**:
  - `agent_runtime/tool_factory.py:164-170` — `async def scrape_url` 내 `BeautifulSoup(resp.text,"html.parser")` + `get_text()`. 수 MB HTML 파싱을 루프에서.
  - `routers/skills.py:237` → `skills/package_exporter.py:12-34` — async 핸들러가 동기 zip 빌드 직접 호출.
  - `routers/skill_files.py:40, 53` — 동기 파일 읽기(경미). `image_service.py:139` 정적 PNG 최초 로드 동기(캐시됨, 경미).
  - **참고 (양호)**: `skill_executor.py`, `mcp/client.py`, `skills/service.py`, install/publish의 zip/shutil은 이미 `asyncio.to_thread`. credential 복호는 sub-ms.
- **리팩토링 방안**: `BeautifulSoup` 블록을 sync helper로 분리해 `await asyncio.to_thread(...)`(또는 `resp.text` 길이 상한). `zip_bytes = await asyncio.to_thread(build_installed_skill_zip_bytes, ...)`. skill_files 두 읽기 경로도 `to_thread`.
- **검증**: 대형 HTML/패키지로 scrape·export 중 별도 요청 지연 측정.
- **예상 공수**: **S**

---

### 추가 임무 — 2026-07-03 감사 3건 현재 상태

| 항목 | 판정 | 증거 |
|------|------|------|
| **web_scraper SSRF** (`tool_factory.py`) | **STILL PRESENT (미수정)** | `tool_factory.py:149-162` `scrape_url`이 모델 제공 URL을 검증 없이 `client.get(url)`. 공유 클라이언트가 `follow_redirects=True`(`:102-106`)라 리다이렉트-경유 SSRF 가능. `ipaddress`/`is_private`/`169.254`/`localhost`/allowlist 가드 전무 — localhost·RFC-1918·클라우드 메타데이터 `169.254.169.254`·`file://` 미차단. |
| **rotate_credentials 무한루프** (`scheduler.py:235-251`) | **PARTIALLY FIXED** | OFFSET 제거로 원래 OOM/커서 미전진은 완화. 그러나 **no-progress 무한루프 잔존**: `re_encrypt_with_active_key`가 한 배치(≥`_ROTATION_BATCH=100`) 전부 지속 실패하면 동일 행 재fetch, 종료 가드 `if len(rows) < _ROTATION_BATCH`는 배치가 항상 꽉 차 트립 안 됨 → `while True` 무한. 실패 id 제외/no-progress break/max-iter 캡 필요. |
| **트리거 중복실행** (`trigger_executor.py:80-122`) | **PARTIALLY FIXED** | `execute_trigger`에 DB 동시성 가드 없음(status→running 클레임/idempotency 없음). APScheduler `coalesce=True, max_instances=1` + 리더락으로 단일 프로세스 내 중복은 방지. 하지만 **run-now(`routers/triggers.py:167`)가 APScheduler 우회** → 스케줄 실행 in-flight + 사용자 run-now 동시 = 이중 실행. DB 레벨 `SELECT … FOR UPDATE` 또는 in-flight run 부분 유니크 인덱스 필요. |

---

### 요약 (우선순위별)

- **P1 (채팅 hot path / 전역 블로킹)**: BE-P1(폴링 N+1 하이드레이션), BE-P2(무제한 메시지 로드), BE-P3(폴링 MCP `FOR UPDATE` N+1), BE-P4(bcrypt 루프 블로킹), BE-P5(SSE 이벤트당 중복 비용)
- **P2**: BE-P6(인덱스 5건), BE-P7(checkpointer 풀+엔진 풀), BE-P8(health history 팽창), BE-P9(MCP 폴 직렬), BE-P10(설치 N+1), BE-P11(인제스트 N+1), BE-P12(무제한/offset 페이지네이션)
- **P3**: BE-P13(async 블로킹 파싱/zip)

**가장 레버리지 큰 4개**: BE-P4(bcrypt `to_thread`, 공수 S, 전역 영향) → BE-P1/BE-P3(폴링 N+1 제거, 공수 S~M) → BE-P6(인덱스 마이그레이션, 공수 S) → BE-P7(풀 상향, 공수 S).

**보안 주의**: web_scraper SSRF는 성능 아닌 **미수정 보안 이슈**로 별도 트랙 권고. rotate_credentials·트리거 중복은 부분 수정 상태로 잔여 리스크 존재.

---

## 6. 백엔드 — 중복 (BE-D)

### [BE-D1] 소유권 조회+None체크→not_found() 패턴 라우터 전면 중복
- **우선순위 제안**: P1
- **카테고리**: 중복
- **증거**: `conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)` + `if conv is None/not conv: raise conversation_not_found()` 3줄 블록이 **13개 파일 30개 호출부**에서 반복:
  - `conversation_traces.py:52-54, 67-69`
  - `conversation_branches.py:118-120, 224-226`
  - `conversation_files.py:35-37, 51-53`
  - `conversation_messages.py:142-144`, `conversation_runs.py:110-112, 132-134`
  - 그 외 `conversation_run_cancel/ag_ui/followup/crud`, `artifacts.py`, `shares.py`
  - 별도로 `agents.py`는 `get_agent(db, agent_id, user.id)`+`agent_not_found()`를 **6회**(185/200/236/268/297/316) 반복, `assistant.py`도 동일 getter 사용
- **문제점**: (1) None 체크가 `if conv is None`과 `if not conv` 두 형태로 섞여 있음 — 스타일 드리프트. (2) 신규 대화 엔드포인트 추가 시 소유권 가드를 빼먹기 쉬움(누락 시 IDOR). (3) enumeration-oracle 통일 규칙(404 단일 응답)이 각 호출부 수동 준수에 의존 — 한 곳이라도 403 raise하면 규칙 붕괴.
- **리팩토링 방안**:
  1. `app/dependencies.py`에 소유권 리졸버 의존성 팩토리 추가:
     ```python
     def owned_conversation(
         conversation_id: uuid.UUID,
         db: AsyncSession = Depends(get_db),
         user: CurrentUser = Depends(get_current_user),
     ) -> Conversation:  # async
         conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
         if conv is None:
             raise conversation_not_found()  # 404 단일 응답 규칙 한 곳에 봉인
         return conv
     ```
     엔드포인트는 `conv: Conversation = Depends(owned_conversation)`로 path param + 소유권 + 404를 한 번에 흡수.
  2. **한 도메인 먼저**: conversation 계열(가장 밀집) → 검증 후 agents(`owned_agent`)로 확산. 각 도메인 getter는 이미 존재(`get_owned_conversation`, `get_agent`)하므로 의존성 래핑만.
  3. **과추상화 방지**: "모든 리소스 하나의 제네릭 의존성"으로 통합하지 말 것 — path param 이름/getter/에러 팩토리가 도메인마다 달라 제네릭화하면 오히려 복잡. 리소스별 얇은 의존성 함수 하나씩만.
- **검증**: `uv run pytest tests/test_conversation_*.py tests/test_agents.py tests/test_multiuser_isolation.py` (소유권 404 회귀 커버), `uv run ruff check .`
- **예상 공수**: M (getter는 재사용, 라우터 시그니처 치환이 물량)

### [BE-D2] error_codes.py 팩토리 존재하는데 raw HTTPException 404/403 드리프트
- **우선순위 제안**: P1
- **카테고리**: 중복 (+ 규칙 위반)
- **증거**: `app/error_codes.py`에 `credential_not_found()`, `tool_not_found()`, `model_not_found()` 등 구조화 팩토리(KOR 메시지 + 에러코드)가 **이미 존재**하지만, 다수 라우터가 raw `HTTPException(status_code=404, detail="...")`(ENG 소문자)로 우회:
  - `credential not found` raw: `credentials.py:110,247,422,735`, `models.py:282,324`, `mcp.py:357,365`, `health.py:226` — **9곳** (팩토리 `credential_not_found()` 있음)
  - `tool not found`: `tools.py:73`, `mcp server not found`: `mcp.py:99`, `health.py:229`
  - `super_user required`: `models.py:110` (403 raw), `forbidden`: `credentials.py:740` (403 raw)
  - 라우터 raw 404/403 총 **24곳** (`error_codes` 미경유)
- **문제점**: 같은 "not found" 의미가 **두 가지 응답 스키마**로 나감 — 구조화 에러(`{code, message}` KOR) vs raw `{detail: "credential not found"}` ENG. 프론트가 에러코드로 분기하면 raw 경로는 코드 없이 새어나가 처리 불가. 또 raw 문자열이 리소스 유형을 노출("credential"/"mcp server")해 enumeration 힌트가 될 수 있음.
- **리팩토링 방안**:
  1. 신규 팩토리 없이 **기존 error_codes 팩토리로 치환**만: `raise HTTPException(status_code=404, detail="credential not found")` → `raise credential_not_found()`. 팩토리 미존재분(`unknown definition '{key}'`, `system credential not found`)만 error_codes.py에 소량 추가.
  2. `models.py:110`의 `super_user required` 403은 가능하면 `Depends(require_super_user)`로 승격(단, `include_hidden`처럼 조건부 gate라 파라미터 의존 → 그 경우 `ForbiddenError` 팩토리로만 통일).
  3. **가드레일**: 라우터에서 `HTTPException(status_code=404|403` 직접 사용 금지 컨벤션 + ruff custom rule 혹은 grep CI 체크로 회귀 차단.
- **검증**: `uv run pytest tests/test_credentials*.py tests/test_tools.py tests/test_mcp*.py tests/test_models.py` (응답 스키마 단언 갱신 필요), `uv run ruff check .`
- **예상 공수**: S~M

### [BE-D3] audit_service.record_event self-owned 신원 kwargs 보일러플레이트
- **우선순위 제안**: P2
- **카테고리**: 중복
- **증거**: `actor_user_id=user.id`가 **16개 라우터 41개 호출부**에 등장(`agents.py:152,205,240,271`, `tools.py`, `mcp.py`, `credentials.py`, `triggers.py`, `marketplace.py`, `shares.py` 등). 대부분 actor==owner==target_owner인 self-action이라 매 호출마다 아래 6~7개 kwargs가 그대로 반복 (agents.py:152-166 대표):
  ```python
  actor_type="user", actor_user_id=user.id, actor_email_snapshot=user.email,
  owner_user_id=user.id, owner_email_snapshot=user.email,
  target_owner_user_id=user.id, outcome="success", request=request,
  ```
  `record_event` 시그니처는 **24개 kwargs**(audit_service.py:86).
- **문제점**: 신원 필드를 손으로 6개씩 채우다 하나(예: `owner_email_snapshot`)를 빠뜨리면 감사 로그 필드 누락이 조용히 발생. `actor_type="user"` 하드코딩 반복도 오타 리스크.
- **리팩토링 방안**:
  1. audit_service에 self-action 편의 래퍼 추가:
     ```python
     async def record_self_event(
         db, user: CurrentUser, *, action: str, target_type: str,
         target_id, target_name: str | None = None,
         outcome: AuditOutcome | str = "success",
         request: Request | None = None, metadata: dict | None = None,
     ) -> AuditEvent:
         return await record_event(
             db, actor_type="user", actor_user_id=user.id,
             actor_email_snapshot=user.email, owner_user_id=user.id,
             owner_email_snapshot=user.email, target_owner_user_id=user.id,
             action=action, target_type=target_type, target_id=target_id,
             target_name_snapshot=target_name, outcome=outcome,
             request=request, metadata=metadata,
         )
     ```
  2. actor≠owner인 소수 케이스(관리자가 타 유저 리소스 조작, marketplace 등)는 기존 full `record_event` 유지 — 래퍼는 self-action에만.
  3. **과추상화 방지**: 래퍼는 "가장 흔한 한 조합"만 흡수. 파라미터를 다시 24개로 부풀리지 말 것.
- **검증**: `uv run pytest tests/test_audit*.py` + 각 도메인 감사 테스트, `ruff`
- **예상 공수**: M (호출부 41곳 치환)

### [BE-D4] 라우터별 _load_owned 헬퍼 + system-or-owned 쿼리 술어 중복
- **우선순위 제안**: P2
- **카테고리**: 중복
- **증거**: 파일마다 자체 소유권 로더 정의: `tools.py:62 _load_owned`, `mcp.py:92 _load_owned`, `credentials.py:107 _load_owned`, `models.py:277 _load_owned_credential`, `uploads.py:93 _get_owned_attachment`, `shares.py:63 _require_owned_conversation` — **6개**. 또 "시스템(NULL) 또는 내 소유" 술어 `or_(Tool.user_id == user_id, Tool.user_id.is_(None))`가 **7곳** 복붙: `tool_service.py:34`, `agent_service.py:246,428`, `agent_blueprint_service.py:294`, `builder_service.py:295`, `tools.py:68,103`.
- **문제점**: `_load_owned`들이 서로 **다른 소유권 의미**를 가짐 — tools=system-or-owned, mcp=strict owned, credentials=`get_for_user`(is_system=False 필터 위임), uploads=strict+404-collapse. 겉보기 동명이라 향후 "다 똑같겠지" 하고 잘못 통합/수정할 위험. system-or-owned 술어는 7곳 중 한 곳만 조건 바뀌면(예: enabled 필터 추가) 정책 드리프트.
- **리팩토링 방안**:
  1. 술어를 모델 헬퍼로 추출(가장 안전한 최소 단위):
     ```python
     # app/models/tool.py 등
     def visible_to(user_id: uuid.UUID):  # system(NULL) + owned
         return or_(Tool.user_id == user_id, Tool.user_id.is_(None))
     ```
     7개 호출부가 `Tool.visible_to(user_id)`를 공유 → 정책 단일 지점.
  2. `_load_owned` 계열은 **BE-D1의 의존성 팩토리로 흡수**하되, 소유권 의미 차이를 `system_visible: bool` 플래그로 명시:
     ```python
     def owned_tool(..., system_visible=True): ...   # tools
     def owned_mcp_server(..., system_visible=False): ...  # strict
     ```
  3. **과추상화 방지**: 단일 제네릭 `load_owned(Model, id, user)`로 뭉치지 말 것 — 소유권 의미(system 포함 여부, 404-collapse 여부)가 리소스마다 달라 플래그 폭발. 술어 추출(1)만으로도 큰 이득.
- **검증**: `uv run pytest tests/test_tools.py tests/test_mcp*.py tests/test_agent_service*.py`, `ruff`
- **예상 공수**: S(술어 추출) + M(로더 통합, BE-D1과 병행)

### [BE-D5] keyset 커서 인코딩/정규화 + 페이지네이션 파라미터 중복·불일치
- **우선순위 제안**: P3
- **카테고리**: 중복
- **증거**: `_encode/_decode_*_cursor`가 서비스마다 재구현: `chat_service.py:656/674`(JSON+base64), `artifact_service.py:88/92`(separator 문자열), `audit_service.py`(커서 로직 보유). timestamp UTC-naive 정규화 `astimezone(UTC).replace(tzinfo=None)`가 `chat_service.py:691`(인라인)과 `artifact_service.py:82 _normalize_cursor_datetime`(헬퍼)에 복붙. 또 Query 선언 `limit: int = Query(default=…, ge=1, le=…)`가 **12+ 라우터**에서 상한 제각각: 100(`conversation_crud:82`), 200(`credentials:457`, `marketplace:178`, `health:162`), 100(`artifacts:171`), 100(`audit:49`).
- **문제점**: (1) 커서 timestamp 정규화가 두 곳 — 한 곳만 tz 버그 고치면 다른 곳은 여전히 aware/naive 불일치로 keyset 페이지가 어긋남(중복/누락 행). (2) limit 상한이 API마다 달라 클라이언트가 예측 불가. 커서 포맷도 2종(JSON vs separator)이라 공유 디코더 불가.
- **리팩토링 방안**:
  1. `app/services/pagination.py` 신설 — 정규화만 우선 공유(리스크 최소):
     ```python
     def normalize_cursor_dt(value: datetime) -> datetime:
         return value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone(UTC).replace(tzinfo=None)
     ```
     `chat_service`/`artifact_service`가 공유.
  2. 커서 포맷 통일(선택): `(sort_value, id)` 튜플 → base64(JSON) 인코더/디코더 1개로 수렴. **단 chat_service의 scope/is_pinned 복합 커서는 도메인 특수** → 무리한 통일 금지(의도된 분리).
  3. 페이지네이션 Query 상한을 공용 상수(`DEFAULT_PAGE_LIMIT`, `MAX_PAGE_LIMIT`)로 통일하되, 실제 필요가 다른 곳(marketplace 대량 목록 200)은 유지.
- **검증**: `uv run pytest tests/test_conversation_crud*.py tests/test_artifact*.py tests/test_audit*.py` (keyset 경계 테스트), `ruff`
- **예상 공수**: S(정규화 공유) / M(커서 포맷 통일 시)

### [BE-D6] 도구 정의 러너의 인증+HTTP 호출 마이크로 패턴 중복
- **우선순위 제안**: P3
- **카테고리**: 중복
- **증거**: `app/tools/definitions/` 러너들이 동일 5단계 반복 — `cred_def = credential_registry.require("<key>")` → `apply_authentication(cred_def.authenticate, {...}, ctx.credentials)` → `response = await ctx.http_client.request(**request_opts)` → `response.raise_for_status()` → `response.json()`:
  - `naver_search.py:56-68`, `google_search.py:34-42`, `gmail_send.py:37-49`, `google_calendar_event.py:47-55`, `http_request.py:61-73`(변형) — **5~6개 파일**
- **문제점**: 러너마다 에러 처리(raise_for_status 후 상태/바디 조립)가 손으로 반복 — 하나는 `http_status` 포함, 하나는 미포함 등 응답 shape 미세 드리프트. 신규 도구 추가 시 인증 주입 한 줄 빠뜨리면 인증 없는 요청.
- **리팩토링 방안**:
  1. 공용 헬퍼(이미 `apply_authentication`은 추출돼 있으니 그 위 얇은 래퍼):
     ```python
     # app/tools/http_runner.py
     async def authed_json_request(ctx, cred_key: str, *, method, url, params=None, json=None):
         cred_def = credential_registry.require(cred_key)
         opts = apply_authentication(cred_def.authenticate, {"method": method, "url": url, "params": params, "json": json}, ctx.credentials)
         resp = await ctx.http_client.request(**{k: v for k, v in opts.items() if v is not None})
         resp.raise_for_status()
         return resp
     ```
  2. **과추상화 방지**: naver의 `_format_items`/HTML strip처럼 도구별 후처리는 러너에 남김 — 헬퍼는 "인증+요청+raise+resp 반환"까지만. 응답 조립(`{"http_status":…, "items":…}`)은 각 러너 자유.
  3. `naver_search.py`는 이미 파일 내부 `_make_runner`/`_common_parameters`로 잘 팩토링됨 — 크로스파일 러너 헬퍼만 추가.
- **검증**: `uv run pytest tests/test_tool_definitions*.py tests/agent_runtime/` (도구 실행 테스트), `ruff`
- **예상 공수**: S

### [BE-D7] 테스트 픽스처 중복 — Model/Agent 시드 팩토리 부재
- **우선순위 제안**: P2
- **카테고리**: 중복
- **증거**: `tests/conftest.py`에 `make_user`(217), `make_refresh_token`(240), `client`/`db`/`TEST_USER_ID` 공유 픽스처는 존재하나 **Model/Agent/Conversation 시드 픽스처가 없음**:
  - `Model(provider="openai", model_name="gpt-4o", …)` 한 줄이 **37개 파일 41회** 복붙
  - 로컬 `_seed_agent`/`_make_agent` 헬퍼가 **21개 파일 ~24개** 정의 (`test_memory_router.py:16`, `test_draft_conversation_gc.py:28`, `test_assistant_router.py:21`, `test_agent_api_control_plane.py:12`, `test_conversation_run_service.py:24`) — 이름/프롬프트만 다르고 구조 동일
  - 인라인 `User(id=TEST_USER_ID, …)` **63회**인데 `make_user`는 5개 파일만 사용 (헬퍼 있는데 미채택)
  - `/api/auth/register`+쿠키/CSRF 추출 로컬 `_register`/`_login` 헬퍼 **7개 파일** (`test_auth_login.py:26`, `test_multiuser_isolation.py:57`, `test_csrf.py:19` 등)
- **문제점**: 스키마/모델 필드 변경 시(예: Model에 필수 컬럼 추가) 37+21개 파일을 일괄 수정해야 함 — 실제 스키마 마이그레이션 때마다 테스트 붕괴. `make_user`가 이미 있는데 안 쓰는 건 팩토리 채택 실패 신호.
- **리팩토링 방안**:
  1. `conftest.py`에 기존 `make_user` 스타일 factory-as-fixture 추가: `make_model(db, *, provider="openai", model_name="gpt-4o", …)`, `make_agent(db, *, user_id=TEST_USER_ID, model_id=…, name=…)`, 결합 `seed_agent(db) -> (user, model, agent)`.
  2. `raw_client` 기반 `login_as`/`registered_session` 픽스처로 7개 auth 헬퍼 통합 (ORM 팩토리와 **분리 유지** — 실제 쿠키/CSRF 경로 검증이라 목적 다름).
  3. **한 번에 다 치환 금지**: 신규 픽스처 도입 후, 새 테스트부터 강제 + 기존은 스키마 변경 시 점진 마이그레이션. `_make_agent_item*`(marketplace 객체 그래프), credential-specific `_make_agent_with_model`은 의도된 분리 → 건드리지 말 것.
  - 대략 231개 top-level 테스트 중 **80~90개**가 인라인 ORM 시드 → 팩토리 채택 대상.
- **검증**: `uv run --with pytest-xdist pytest -q -n 4` (전체 그린 확인)
- **예상 공수**: M~L (물량은 크나 기계적)

### [BE-D8] Response 스키마 id/timestamp 필드 + ConfigDict 반복
- **우선순위 제안**: P3
- **카테고리**: 중복
- **증거**: `id: uuid.UUID` / `user_id` / `created_at: datetime` / `updated_at: datetime` 필드 선언이 **17개 스키마 파일 184회**, `ConfigDict(from_attributes=True)`가 별도 9회. 대표: `tool.py:64-77 ToolInstanceResponse`(id/created_at/updated_at), `agent.py`, `mcp.py`, `credential.py`, `trigger.py`, `model.py` 등 모든 `*Response`가 동일 3~4필드 재선언.
- **문제점**: 낮음 — 실 리스크는 크지 않으나, ORM Response 공통 베이스가 없어 `from_attributes` 설정을 개별 클래스가 각자 관리(일부 누락 시 `model_validate` 실패). 타임스탬프 직렬화 규약(예: tz-aware 통일)을 바꾸려면 17파일 수정.
- **리팩토링 방안**:
  1. `app/schemas/base.py`에 얇은 믹스인:
     ```python
     class ORMModel(BaseModel):
         model_config = ConfigDict(from_attributes=True)
     class TimestampedResponse(ORMModel):
         id: uuid.UUID
         created_at: datetime
         updated_at: datetime
     ```
     `*Response`가 `TimestampedResponse` 상속 → id/timestamp/config 흡수.
  2. **과추상화 방지 (중요)**: `user_id`는 nullable 여부가 리소스마다 달라(`user_id: uuid.UUID | None` vs 필수) 베이스에 넣지 말 것. 필드 조합이 제각각인 스키마는 상속 강요 금지 — `ORMModel`(config만) 정도가 안전선. **이 항목은 이득이 가장 작으니 다른 P1/P2 이후 여유 시에만.**
- **검증**: `uv run pytest tests/` (직렬화 회귀), `ruff`
- **예상 공수**: S

---

**중복 아닌 것 (의도된 분리 — 제외)**:
- **credential 보간(`resolve_deep`)**: `app/credentials/interpolation.py` 단일 함수로 이미 완전 중앙화 — mcp/client·mcp/auth·credentials/tester·authenticate가 모두 재사용(11 call sites). 조치 불필요.
- **제네릭 베이스 CRUD 서비스**: `db.add/commit/refresh` 3종이 ~35회 반복하나 각 서비스 create/update가 스케줄러 sync·검증·감사 등 도메인 로직 다수 보유. 제네릭 BaseService 도입은 Simplicity First 위반 → **권장 안 함**. 필요하면 `commit_refresh(db, obj)` 미니 헬퍼 정도만.
- **chat_service scope/is_pinned 복합 커서**: 도메인 특수 필드라 통일 대상 아님(BE-D5 참고).

---

## 7. 프론트엔드 — 구조/중복 (FE-S)

**요약 판단**: 공용 프리미티브 레이어(`components/shared/*`: `DialogShell` 38개 사용처, `ResourcePage`/`SettingsShell`/`FormFieldShell`/`base-detail-dialog`, `lib/query-keys/*` 팩토리)가 이미 성숙. 진짜 문제는 **채택 불일치(half-done commonization)**와 **채팅 런타임 이중화 + 초거대 파일 3개**. API 3계층(`apiFetch<T>` → `xxxApi` → `use-xxx`)은 깨끗해 findings 아님.

핵심 수치: 상위 3개 파일 5,766줄(use-moldy-langgraph-stream.ts 2941 / assistant-thread.tsx 1458 / use-chat-runtime.ts 1367). 프로젝트 규칙(coding-style.md: 파일 ≤800줄) 크게 초과.

### [FE-S1] 채팅 런타임 이중화 — legacy `useChatRuntime` vs v3 `useMoldyLangGraphStream` 완전 병렬 구현 공존
- **우선순위 제안**: P1 / 구조
- **증거**: 스위치 `lib/chat/runtime-mode.ts:3-5`(기본 langgraph_v3) → `conversations/[conversationId]/page.tsx:108,324` → `components/chat/chat-runtime-section.tsx:116`이 `LegacyRuntimeSection`(:174 useChatRuntime) / `LangGraphRuntimeSection`(:207 useMoldyLangGraphStream) 분기. legacy는 풀 병렬 구현: SSE 수동 파싱 `use-chat-runtime.ts:554-914` vs v3 `useStream`+`useChannel`. HITL/edit/regenerate/attach/stop/usage/artifact 전 개념 양쪽 중복. legacy 직접 소비처 4곳: `test-chat-panel.tsx:39`, `assistant-panel.tsx:109`, `app/agents/new/conversational/page.tsx:115`, chat-runtime-section legacy 분기.
- **문제점**: 채팅 기능 추가마다 두 SSE 해석 경로 동시 유지. 회귀 위험 2배.
- **리팩토링 방안** (수렴 로드맵):
  1. **메인 채팅 legacy 분기 제거**: `chat-runtime-section.tsx`의 useLangGraphRuntime false 폴백(draft/no-conversation)을 없애고 draft는 항상 `useLanggraphDraftConversation`(page.tsx:179-189)으로 실 conversation 부트스트랩. `runtime-mode.ts` legacy escape hatch 제거.
  2. **남은 3개 소비처 블로커**: builder(new/conversational)는 builder_v3 세션 프로토콜이라 백엔드에 builder 세션용 LangGraph thread 엔드포인트 생기기 전 이관 불가(legacy 유지 명시). test-chat-panel/assistant-panel은 ephemeral(streamAssistant, onMessagesCommit 로컬 히스토리) — v3 등가물 없음. 실 conversation 재호스팅 또는 legacy 전용 축소판 격리.
  3. **최종**: `use-chat-runtime.ts`를 `lib/chat/legacy/`로 격리 이동, 소비처 3곳으로 명시적 축소. 삭제는 백엔드 프로토콜 통일 후.
  - 테스트: `use-chat-runtime-*.test.tsx` 5종 legacy 전용 유지. v3 transport mock `createMockTransport()` 헬퍼 통일.
- **검증**: `pnpm vitest run` / `pnpm build` / e2e `chat-*.spec.ts` 전체.
- **예상 공수**: L (1단계 M, 소비처 은퇴 L, 백엔드 통일 XL)

### [FE-S2] `use-moldy-langgraph-stream.ts` (2941줄) 분해
- **우선순위 제안**: P1 / 구조
- **증거**: `1-79` 임포트, `81-1991` ~1900줄 모듈레벨 순수 헬퍼, `1993-2941` 훅 본체(~948줄). 이미 잘 위임된 개념(artifact :2384, usage :2389, compaction :2395, data-ui :2425, memory :2517, subagent-names :2518, deepagents-state :2132, transport :2091, checkpoint-fork :2578, activity :2127). 인라인인데 분리 대상: thread-state 파서 4종(:234-309), terminal-notice append(:512-532), pending-edit/reload 렌더 상태머신(:137-174, :644-1064), sticky 메시지 캐시(:534-642, 1441-1951). 상태: useState 8 / useRef 9 / useCallback 20 / useMemo 23 / useEffect 12.
- **리팩토링 방안** (6 모듈 + 2 훅, 부모 ~200줄 컴포지션 셸):
  ```
  lib/chat/langgraph-runtime/
    thread-state-checkpoints.ts   ← (기존) + 파서 4종 (:234-334)   [순수, seam 낮음]
    terminal-notice.ts            ← (기존) + appendTerminalRunNotice (:512-532)
    sticky-messages.ts            ← 신규: 모듈-글로벌 Map 캐시 (:534-642,1441-1951)  [싱글턴 유지 필수]
    pending-checkpoint-render.ts  ← 신규: edit/reload 순수 상태머신 (:398-1344)  [seam 高]
    use-thread-hydration.ts       ← 신규: postRun 폴링 3 effect (:2173-2256,2433-2492)  [onCancel의 hydrationCanceledRef 순서]
    use-hitl-decisions.ts         ← 신규: coordinator refs + resume API (:2773-2930)  [resolvedInterrupts는 부모 소유]
    use-draft-submit.ts           ← 신규 (:553-585,1346-1370)
  ```
  - 점진 순서: 순수 파서/notice → sticky-messages → pending-render 순수 함수 먼저 → hydration 훅 → HITL 훅.
  - **지킬 seam**: ① 공유 ref `latestVisibleMessagesRef`(:2035), `pendingEditBase*Ref`(:2036-37) 복제 금지. ② `handleThreadState`(:2078)는 단일 mutation 퍼널 — 부모 유지(테어링 방지). ③ `resolvedInterrupts` 사이클 상태는 부모 소유. ④ `onNew/onEdit/onReload`의 `flushSync`(:2664,2696,2740) 동기 관측성 유지.
- **검증**: `pnpm vitest run src/lib/chat/langgraph-runtime` / tsc / e2e chat 회귀.
- **예상 공수**: L

### [FE-S3] `assistant-thread.tsx` (1458줄) 분해
- **우선순위 제안**: P2 / 구조
- **증거**: 메시지 파트 렌더 :179-364, 아티팩트/compaction :366-438, 메시지 액션 :440-554,702-740, 브랜치 피커 :556-700(자기완결), 메시지 컴포넌트 맵 :830-1039, Cmd+F :1046-1056, ThreadComposer :1167-1345, StopButton/AttachmentChip/TokenBar :1347-1458.
- **리팩토링 방안**:
  ```
  components/chat/thread/
    message-parts.tsx / message-artifacts-compaction.tsx / message-actions.tsx
    branch-picker.tsx (첫 추출 권장) / message-components.tsx / thread-composer.tsx
    assistant-thread.tsx (셸)
  ```
  순서: branch-picker → thread-composer → message-*. HITL 플러밍은 컨텍스트 주입 유지.
- **예상 공수**: M

### [FE-S4] `approval-card.tsx` (701줄) 분해 + `use-approval-form` 중복
- **우선순위 제안**: P2 / 구조+중복
- **증거**: ArgsPreview(:223-272), ArgsEditor(:281-374), 결정 제출(:383-456), 버튼 3종(:588-654), ApprovalBadge(:146-167), 래퍼(:664-699). 중복①: `approval-card` 자체 `handleDecision`(:420-456) vs `tool-ui/use-approval-form.ts`(prompt-approval-ui.tsx:23 소비) — 공유 코드 0. 중복②: 헤더 셸 approval-card.tsx:684-696 ≈ grouped-approval-card.tsx:43-62.
- **리팩토링 방안**: 1. `approval-args-editor/preview`, `decision-buttons`, `approval-badge` 순수 추출. 2. `useApprovalDecision` 훅으로 `use-approval-form.ts`와 통합. 3. `ApprovalCardShell` 공유. 테스트 주의: "restores redacted placeholders" 테스트는 un-redacted args 주입 → 실제 redacted 경로로 교정.
- **예상 공수**: M

### [FE-S5] 비대 page — `settings/memory/page.tsx`(649) · `agents/new/template/page.tsx`(617)
- **우선순위 제안**: P2 / 구조
- **증거**: memory: CreateMemoryCard(:297-419), MemoryRecordItem(:421-576), PolicyCard(:173-295), ToggleField/SelectField(:578-649, FormFieldShell 재발명). template: 오케스트레이터(:48-259), `filtered`(:77-94)와 `filteredBlueprints`(:96-119) 중복, TemplateCard(:375-462)와 BlueprintCard(:464-545) ~80% 동일.
- **리팩토링 방안**: memory → `_components/` 추출 + `useDirtyDraft` 소훅 + FormFieldShell 흡수. template → `_hooks/use-template-gallery.ts` + 단일 `GalleryItemCard` + `sortByKey` util.
- **예상 공수**: M

### [FE-S6] create/edit 다이얼로그 셸 8+회 복붙 + 미사용 `BaseDetailDialog`
- **우선순위 제안**: P2 / 중복
- **증거**: `credential-create-modal.tsx`(:54-57,:96-112), `model-add-dialog.tsx`(:85-128), `model-edit-dialog.tsx`(11 useState :51-63), `skill-create-tabs.tsx`, `tool-create-dialog.tsx`, `mcp-import-dialog.tsx` 동일 셸. **죽은 추상화**: `components/shared/base-detail-dialog.tsx`(130줄) 소비처 0. agent create(`agents/new/manual/page.tsx:62-103`)가 edit의 `useAgentSettingsDraft`(settings/page.tsx:70) 우회 — 13필드 raw useState 재선언.
- **리팩토링 방안**: 1. `useResourceFormDialog<T>` 소훅(prop re-seed는 remount `key` 패턴). 2. detail 다이얼로그를 `BaseDetailDialog`로 이관(아니면 삭제). 3. `useAgentSettingsDraft` create/edit 겸용 일반화 + `AgentSettingsHeader` 공유.
- **예상 공수**: L

### [FE-S7] `lib/types/index.ts` (775줄) — 혼합 바렐(재수출 14 + 로컬 정의 67)
- **우선순위 제안**: P2 / 구조
- **증거**: :6-19 재수출 + :23-775 Agent 도메인 타입 67개 로컬 인라인. AGENTS.md "barrel export 지양" 상충.
- **리팩토링 방안**: `types/agent.ts`, `types/chat.ts`, `types/middleware.ts`로 이동. index.ts는 재수출만(호환 유지) → 점진 직접 import 전환.
- **예상 공수**: M

### [FE-S8] Query 키 팩토리 드리프트 — 팩토리 13개 존재하나 9개 훅이 인라인 정의
- **우선순위 제안**: P2 / 구조+중복 (빠른 승리 S)
- **증거**: `lib/query-keys/*` 13개 팩토리 존재. 인라인 정의 9개 훅: `use-memory.ts:15`, `use-conversations.ts:39`, use-agent-api, use-artifact-library, use-audit-events, use-conversation-artifacts, use-conversation-files, use-conversation-title, use-share, use-system-llm-settings. AGENTS.md 규칙 위반.
- **리팩토링 방안**: 인라인 `*Keys`를 `lib/query-keys/`로 이동(기존 `toolQueryKeys` 계층 형식). 테스트의 리터럴 배열 단언은 동일 배열 반환이므로 무변경.
- **예상 공수**: S

### [FE-S9] 디렉토리 배치 불일치 — `features/`는 schedules 하나뿐
- **우선순위 제안**: P3 / 구조
- **증거**: AGENTS.md 규칙(route-only→`_components/`, 다중 라우트→`features/<domain>/`) 있으나 `features/`엔 schedules만. agent 도메인은 `components/agent/`(8) + `app/agents/.../settings/_components/`로 이원화. `src/hooks` vs `src/lib/hooks` 이원화도 존재.
- **리팩토링 방안**: 규칙 준수 강제(lint:frontend-architecture) + 도메인 단위 PR로 점진 이주(`components/chat/` → `features/chat/` 등).
- **예상 공수**: L (전량) / 도메인당 S~M

### [FE-S10] 타입 드리프트 — 백엔드 Pydantic ↔ `lib/types/*` 수동 동기화, OpenAPI codegen 부재
- **우선순위 제안**: P3 / 구조
- **증거**: openapi/codegen 스크립트 없음. FastAPI `/openapi.json` 자동 제공.
- **리팩토링 방안**: `openapi-typescript` 도입 — `pnpm gen:types` → `lib/types/api.gen.ts`, 도메인 타입은 `components['schemas']['AgentRead']` 파생. 타입만 생성(전면 orval 불필요). CI drift 체크.
- **예상 공수**: M

### 부록: findings 아님 (강점)
- API 클라이언트 3계층: 일관·간결. 제네릭 `useResourceQuery` 도입은 과추상화.
- Jotai stores: 8개 atom 파일 대체로 일관.
- DialogShell 채택: raw DialogContent 잔존 2개뿐.
- mcp-servers 위저드 / agent settings: 이미 잘 분해됨(참고 모델).

**우선순위 요약**: P1 = FE-S1, FE-S2. P2 = FE-S3·S4·S5·S6·S7·S8. P3 = FE-S9·S10. 빠른 승리 = FE-S8. 임팩트 최대 = FE-S1 + FE-S2.

---

## 8. 프론트엔드 — 성능/디자인/접근성 (FE-P/FE-D)

**먼저 — 이미 양호(수정 불필요, 오탐 방지용)**
- **디자인 토큰**: `pnpm lint:design-system` 가드가 강력히 작동. 제품 코드에 raw hex/arbitrary typography 사실상 없음 (`text-[..px]` 0건, product hex는 data-viz 팔레트 + 벤더 로고뿐).
- **무거운 뷰어 번들**: mermaid/docx/xlsx/pptx/hwp/pdf/react-syntax-highlighter 전부 `lazy()`로 분리됨 (`artifacts/preview-registry.tsx`, `markdown-code-block.tsx:7`).
- **메시지 변환/리스트 레이어**: converter 캐시·fingerprint·per-message memo 정교 (`message-list.ts:215-235`).
- **useAuiState 셀렉터 대부분 준수**.

### [FE-P1] 채팅 컨텍스트(`AssistantThreadDynamicContext`) 값이 스트리밍 토큰마다 churn → 전체 메시지 리렌더
- **우선순위 제안**: P1 / 성능
- **증거**: `components/chat/assistant-thread.tsx:860-883` — `dynamicContextValue`는 `useMemo`지만 의존성에 `activities`, `deepAgentsState` 포함. 토큰마다 새 참조:
  - `deepAgentsState`: `use-moldy-langgraph-stream.ts:2132` `useMemo(selectDeepAgentsState(stream.values ?? {}), [stream.values])`. `stream.values`는 청크마다 새 참조 + `selectDeepAgentsState`(`deepagents-state.ts:187-192`)는 항상 새 `{todos, files}` + 새 배열 반환.
  - `activities`: `use-moldy-langgraph-stream.ts:2124-2131` `reduce`가 새 배열.
  - Provider는 `assistant-thread.tsx:1059`에서 전체 스레드를 감싸고 모든 user/assistant 메시지가 구독(:888, :931/:958).
- **문제점**: 컨텍스트 값 identity가 토큰마다 바뀌어 마운트된 모든 메시지 래퍼 서브트리 재렌더. 대화 길이 N에 비례해 스트리밍 중 jank.
- **리팩토링 방안**: 1. 토큰마다 변하는 필드(`deepAgentsState`, `activities`)를 identity 컨텍스트에서 분리 — 별도 provider 또는 jotai atom으로 옮겨 실제 소비 지점에서만 read. 2. `AssistantThreadDynamicContext`엔 안정 필드만. 3. `selectDeepAgentsState`가 내용 불변 시 이전 참조 재사용(구조적 동등 비교).
- **검증**: React DevTools Profiler 스트리밍 60초 커밋 횟수 before/after.
- **예상 공수**: M

### [FE-P2] 채팅 메시지 스레드 가상화 부재
- **우선순위 제안**: P1 / 성능
- **증거**: `assistant-thread.tsx:1095` `<ThreadPrimitive.Messages>` non-virtualized. `useVirtualizer`/`react-window` 0건.
- **문제점**: 100+ 턴 대화 진입 시 전체 트리 일괄 마운트 → 초기 렌더 지연 + 스크롤 프레임 드랍.
- **리팩토링 방안**: 1. 저비용 — 메시지 `React.memo` + FE-P1 해소. 2. `@tanstack/react-virtual` 도입, 가변 높이 `measureElement`, 하단 고정 정합성. 3. 메시지 N개 임계값 기반 점진 도입.
- **검증**: Profiler 200턴 초기 커밋 + 스크롤 long task, Lighthouse TBT.
- **예상 공수**: L (본체) / S (memo 선조치)

### [FE-P3] 관리 테이블/확장 네비게이터 목록 가상화 부재
- **우선순위 제안**: P2 / 성능
- **증거**: `components/ui/data-table.tsx:261` 전체 행 렌더. 네비게이터 `layout/chat-navigator-agent-group.tsx:78-80` 확장+무한스크롤 시 무제한.
- **리팩토링 방안**: DataTable opt-in row virtualizer(50행 초과 시), 네비게이터 확장 리스트 동일 재사용.
- **예상 공수**: M

### [FE-P4] 활성 런 중 1초 폴링으로 전체 대화 목록 이중 재요청
- **우선순위 제안**: P2 / 성능
- **증거**: `lib/hooks/use-conversations.ts:174-176` + `195-197` 둘 다 `refetchInterval` 1000ms, `use-conversation-runs.ts:20` run 상태도 1s. 스트리밍 시 초당 2회 전체 목록 + 1회 run 상태.
- **리팩토링 방안**: 1. run-status 폴링 단일 채널로 일원화 → 변할 때만 `invalidateQueries`. 2. 전체 페이지 `refetchInterval` 제거 또는 3~5s + `structuralSharing`. 3. 네비게이터 미표시 시 폴링 중지.
- **검증**: Network 탭 60초 요청 수 before/after.
- **예상 공수**: M

### [FE-P5] 페이지 레벨 'use client' 광범위
- **우선순위 제안**: P3 / 성능
- **증거**: 774 파일 중 361개 `'use client'`. `settings/*/page.tsx` 전부 + marketplace 페이지 루트가 client.
- **리팩토링 방안**: 신규 페이지부터 서버 page.tsx + `_components/*-page-client.tsx` 분리 규칙화; 기존 대형 settings는 헤더 서버 추출부터.
- **예상 공수**: M (전면) / S (신규 규칙만)

### [FE-P6] 죽은 의존성 chart.js + next/image 미사용
- **우선순위 제안**: P3 / 성능
- **증거**: `package.json` `chart.js ^4.5.1` — src import 0건. `next/image` 0건, raw `<img>` 9건.
- **리팩토링 방안**: chart.js 제거; 백엔드-서빙 아바타는 `images.remotePatterns` + `next/image`; 외부 썸네일은 `<img>` 유지.
- **예상 공수**: S

### [FE-P7] useAuiState 셀렉터 미세 위반 + phase-timeline O(n) 토큰당 스캔
- **우선순위 제안**: P3 / 성능
- **증거**: `assistant-thread.tsx:605-608` BranchPicker `meta` 셀렉터 `?? {}` 새 객체. `tool-ui/phase-timeline-ui.tsx:157-160` 토큰마다 전체 메시지 O(n) 스캔.
- **리팩토링 방안**: `EMPTY_BRANCH_META` 모듈 상수; phase-timeline latest id를 런타임 이벤트에서 도출.
- **예상 공수**: S

### [FE-D1] 채팅/대시보드/공유 라우트 에러 바운더리 완전 부재 (2026-07-03 감사 유효 확인)
- **우선순위 제안**: P1 / 디자인(신뢰성)
- **증거**: `ErrorBoundary` 0건. `app/error.tsx`·`app/global-error.tsx` 없음. route `error.tsx`는 settings/tools/marketplace/skills/mcp-servers 5곳만 — **agents(핵심 채팅), conversations, shared/[shareId], 대시보드 루트, usage/artifacts 미커버**.
- **문제점**: 스트리밍 채팅 렌더 예외 시 화이트스크린 → 앱 셸 전체 붕괴. 공개 공유 링크도 방문자에게 노출.
- **리팩토링 방안**: 1. `app/global-error.tsx` + `app/error.tsx`. 2. `app/agents/error.tsx`, `app/shared/error.tsx` — reset 버튼 + i18n. 채팅은 스트림 레벨 경계 검토. 3. 공용 `RouteError` 프리미티브로 통일.
- **검증**: 의도적 throw 주입 E2E, axe.
- **예상 공수**: M

### [FE-D2] 아이콘 컨트롤 접근성 라벨 누락 (baseline 40건)
- **우선순위 제안**: P2 / 접근성
- **증거**: `scripts/jsx-a11y-baseline.json` 40건: `control-has-associated-label` 26, `anchor-has-content` 7 등. 상위: `chat/trace-debugger-view.tsx`(4), `agent/visual-settings/nodes/agent-node.tsx`(3), `chat/right-rail/chat-right-rail.tsx`(2).
- **리팩토링 방안**: 26건부터 `aria-label`(i18n 키) 추가 → baseline 제거 → 리뷰 체크리스트화.
- **예상 공수**: M

### [FE-D3] 로딩/에러/빈 상태 커버리지 불균형
- **우선순위 제안**: P2 / 디자인
- **증거**: `loading.tsx` 5곳만. agents(채팅)/대시보드/shared/usage/artifacts엔 둘 다 없음. Skeleton 55파일 vs Spinner 38파일 혼재.
- **리팩토링 방안**: 미커버 라우트 `loading.tsx`(관리=스켈레톤, 채팅=전용 셸) → 선택 규칙 문서화 → 공용 `EmptyState`.
- **예상 공수**: M

### [FE-D4] chart-card 테마 비대응 하드코딩 팔레트 (+이중 가드 우회)
- **우선순위 제안**: P3 / 디자인
- **증거**: `components/chat/data-ui/chart-card.tsx:22-29` `CHART_PALETTE` 8개 hex 고정. `fill={seriesColor(index)}` JS 문자열이라 `raw-hex-utility` 가드와 inline-SVG 가드 양쪽 우회. 추가: `dark:` 없는 `bg-white` 3건 — `ui/slider.tsx:45`, `hwp-preview.tsx:147`, `pptx-preview.tsx:108`.
- **리팩토링 방안**: CSS 변수 data-viz 토큰(`--chart-cat-1..8` 라이트/다크) → 팔레트 공유 → 가드 예외 축소. slider/문서 페인 `dark:` 변형 추가.
- **예상 공수**: S

### [FE-D5] i18n 하드코딩 — agent-prism 트레이스 UI 영어 전용 (가드 스킵 영역)
- **우선순위 제안**: P2 / i18n
- **증거**: `scripts/check-static-i18n.mjs`가 `src/components/agent-prism/**` 명시적 스킵(line 33-40). `TextInput.tsx:133`, `Tabs.tsx:112`, `CollapseAndExpandControls.tsx:23/39`, `SearchInput.tsx:13`, `TraceViewerDesktopLayout.tsx:97/113`, `TraceViewerSearchAndControls.tsx:23`, `DetailsView.tsx:73` 등.
- **리팩토링 방안**: 1. 래퍼 레벨에서 label/placeholder prop 주입. 2. 주입 불가 문자열만 최소 fork로 next-intl 키. 3. 스킵 범위 좁히기.
- **예상 공수**: M

### [FE-D6] shadcn/ui 우회 — 채팅 tool-ui 수제 폼 컨트롤 + radio 프리미티브 부재
- **우선순위 제안**: P2 / 디자인
- **증거**: raw `<textarea>` 8건(approval-footer.tsx:57 등), raw text input(approval-card.tsx:335/345/355), raw checkbox(`user-input-ui.tsx:97` — shadcn Checkbox 존재하는데 우회), raw radio(`marketplace/update-strategy-dialog.tsx:131` — **RadioGroup 프리미티브 자체가 없음**).
- **리팩토링 방안**: 1. `RadioGroup` shadcn 프리미티브 신설. 2. tool-ui 폼 컨트롤을 `ui/` 프리미티브로 교체(IME 컴포저 예외). 3. 리뷰 체크리스트/린트 강제.
- **예상 공수**: M

### [FE-D7] 미디어 아티팩트 접근성 — audio/video 캡션·라벨 부재
- **우선순위 제안**: P2 (국소) / 접근성
- **증거**: `components/chat/artifacts/providers/media-preview.tsx:7` `<audio controls>` / `:9` `<video controls>` — `<track>` 없음 + `aria-label` 없음.
- **리팩토링 방안**: 파일명 기반 `aria-label`, 가능한 경우 `<track kind="captions">`.
- **예상 공수**: S

### 요약 우선순위
- **P1**: FE-P1(컨텍스트 churn), FE-P2(스레드 가상화), FE-D1(에러 바운더리)
- **P2**: FE-P3, FE-P4, FE-D2, FE-D3, FE-D5, FE-D6, FE-D7
- **P3**: FE-P5, FE-P6, FE-P7, FE-D4

---

## 9. 테스트/인프라/DevX (IX)

직접 조사 결과 기반 (`.github/`, `docker-compose.yml`, 두 Dockerfile, `backend/pyproject.toml`, `backend/tests/conftest.py`, `frontend/e2e/`, `backend/app/seed/`, `backend/app/main.py`).

**먼저 — 이미 양호 (오탐 방지용)**:
- `backend/tests/conftest.py`(271줄): autouse DB 셋업, `client`/`raw_client`/`db` 픽스처, `make_user`/`make_refresh_token` 팩토리, CSRF 바이패스 — 공유 픽스처 체계 자체는 건강 (채택률 문제는 BE-D7).
- pytest 마커 체계 존재: `[tool.pytest.ini_options]`에 `integration` 마커 + `addopts = "-m 'not integration'"` — live PG 테스트 분리 구조가 이미 있음.
- 두 Dockerfile 모두 multi-stage: backend는 skill-node 빌드 스테이지 + `uv sync --frozen --no-dev`, frontend는 standalone Next 빌드 + 최소 runner. 레이어 순서(의존성 → 소스)도 캐시 친화적.
- 시드 시스템(`app/seed/`, 총 1,257줄): 파일 소규모, upsert 방식 멱등 — 부팅 성능 이슈 없음.

---

### [IX-1] CI 파이프라인 완전 부재 — 모든 게이트가 로컬 수동 실행에 의존
- **우선순위 제안**: **P0** — 이 문서의 모든 리팩토링 PR이 CI 없이는 "전체 그린" 보장을 사람 손에 의존하게 됨. 리팩토링 착수 전 최우선.
- **카테고리**: DevX
- **증거**: `.github/workflows/` 디렉토리 없음(직접 확인). `.pre-commit-config.yaml`도 없음. 반면 게이트로 쓸 도구는 전부 준비됨: ruff(설정 존재), pyright(전체 968 기존 에러 — non-blocking 잡으로 시작, 신규/수정 파일은 파일 단위 클린 유지), pytest 2,500+, vitest 1,183+, eslint 커스텀 가드(`lint:design-system`, `lint:a11y`, `lint:i18n`, `lint:frontend-architecture`).
- **문제점**: 브랜치/PR마다 사람이 전체 스위트를 돌려야 하고, 잊으면 회귀가 main에 도달. CLAUDE.md 메모리에도 "머지 전 vitest 전체 그린 확인" 실패 사례가 기록돼 있음.
- **리팩토링 방안**:
  1. `.github/workflows/ci.yml` 신설 — backend/frontend 2-job 분리, path filter로 무관 변경 스킵:
     ```yaml
     name: CI
     on:
       pull_request:
       push: { branches: [main] }
     jobs:
       backend:
         runs-on: ubuntu-latest
         defaults: { run: { working-directory: backend } }
         steps:
           - uses: actions/checkout@v4
           - uses: astral-sh/setup-uv@v5
             with: { enable-cache: true }
           - run: uv sync --frozen
           - run: uv run ruff check .
           - run: uv run pyright
           - run: uv run --with pytest-xdist pytest -q -n 4
       frontend:
         runs-on: ubuntu-latest
         defaults: { run: { working-directory: frontend } }
         steps:
           - uses: actions/checkout@v4
           - uses: pnpm/action-setup@v4
           - uses: actions/setup-node@v4
             with: { node-version-file: '.node-version', cache: pnpm }
           - run: pnpm install --frozen-lockfile
           - run: pnpm lint
           - run: pnpm vitest run
           - run: pnpm build
     ```
  2. (2단계) e2e는 별도 워크플로우로 nightly 또는 label 트리거 — throwaway PG 서비스 컨테이너 + `E2E_SCRIPTED_MODEL_ENABLED=true E2E_SEED_USER_ENABLED=true` (CLAUDE.md의 E2E 격리 절차 그대로).
  3. (3단계) IX-6의 PG integration 잡 추가.
  4. 브랜치 보호 규칙: main에 두 job required.
- **검증**: 워크플로우 도입 PR 자체가 그린으로 통과하는지 + 의도적 실패 커밋으로 게이트가 실제 차단하는지 확인.
- **예상 공수**: M

---

### [IX-2] pre-commit 훅 부재 — 커밋 단계 자동 게이트 없음
- **우선순위 제안**: P2
- **카테고리**: DevX
- **증거**: `.pre-commit-config.yaml` 없음(직접 확인).
- **문제점**: ruff format/lint 위반, 대형 파일 실수 커밋 등이 CI(도입 후)까지 가서야 발견 — 피드백 루프가 느림.
- **리팩토링 방안**:
  1. `.pre-commit-config.yaml` 추가: `ruff check --fix` + `ruff format`(backend), `eslint --fix`(frontend staged, lint-staged 병용 가능), `check-added-large-files`, `check-merge-conflict`.
  2. `uv run pre-commit install` 안내를 README/CLAUDE.md 세팅 절차에 추가.
  3. 무거운 검사(pyright, vitest)는 pre-commit에 넣지 말 것 — 커밋 속도 보호, CI 담당.
- **검증**: 의도적 lint 위반 커밋이 로컬에서 차단되는지 확인.
- **예상 공수**: S

---

### [IX-3] docker-compose/Dockerfile 프로덕션 하드닝 (감사 지적 부분 유효)
- **우선순위 제안**: P2
- **카테고리**: 인프라
- **증거**: `docker-compose.yml` — PG 비밀번호 평문 `moldy`(:6), 모든 서비스 `restart` 정책 없음, backend/frontend `healthcheck` 없음(postgres만 있음, :12-16), frontend `depends_on: [backend]`가 condition 없음(:71-72). 두 Dockerfile 모두 **non-root USER 미지정**, `HEALTHCHECK` 인스트럭션 없음. (참고: env_file 분리·migration 선행 실행·NEXT_PUBLIC 빌드타임 주입 등은 이미 잘 처리돼 있음.)
- **문제점**: 이 compose 파일이 사실상 프로덕션 배포 경로로도 쓰일 수 있는 구조인데, 컨테이너 재시작·기동 순서·상태 감시가 없어 장애 시 수동 복구 필요. 컨테이너가 root로 실행돼 컨테이너 탈출 시 피해 확대.
- **리팩토링 방안**:
  1. dev/prod 분리: 현 파일은 dev 전용으로 명시하고 `docker-compose.prod.yml` 오버레이 신설 — `restart: unless-stopped`, PG 비밀번호는 `.env` 변수화(`POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?required}`), 포트 바인딩 최소화(PG 5432 외부 노출 제거).
  2. backend에 healthcheck 추가: `test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]` (기존 `/health` 라우터 활용) + frontend `depends_on: { backend: { condition: service_healthy } }`.
  3. Dockerfile: `RUN useradd -m app && chown -R app /app` + `USER app` (backend는 `data/` 볼륨 권한 주의), 두 이미지에 `HEALTHCHECK` 추가.
- **검증**: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` 후 `docker ps` healthy 확인, 컨테이너 kill 후 자동 재시작 확인.
- **예상 공수**: S~M

---

### [IX-4] Alembic 76개 리비전 — squash 검토
- **우선순위 제안**: P3 (충돌·부팅 비용이 임계 미만이므로 서두를 필요 없음)
- **카테고리**: DevX
- **증거**: `backend/alembic/versions/` 76개 파일(M1~M63 + 병합 1개). 신규 환경마다 76개 순차 적용.
- **문제점**: 신규 환경/E2E throwaway 스택 셋업마다 76 리비전 순차 실행(현재 수 초 수준이라 실용 문제는 작음). 병렬 feature 브랜치 간 head 충돌 빈도가 리비전 수에 비례해 증가.
- **리팩토링 방안** (착수 시):
  1. 마일스톤 시점(예: M63)을 기준으로 `alembic upgrade head` 완료된 DB에서 `alembic revision --autogenerate`가 빈 diff인지 먼저 확인(모델↔마이그레이션 싱크 검증).
  2. 새 베이스라인 리비전 1개 생성: 현 head 스키마 전체를 `op.create_table` 세트로 담고 `down_revision=None`.
  3. **기존 배포 DB 경로 보존이 핵심**: 베이스라인 리비전의 `upgrade()`에 "이미 `alembic_version`이 M63이면 스탬프만 갱신" 분기를 넣거나, 구 리비전 체인을 `versions/archive/`로 옮기고 기존 DB는 `alembic stamp <new-baseline>` 절차를 릴리스 노트에 명시.
  4. E2E/CI가 새 베이스라인으로 기동되는지 확인 후 구 체인 제거.
  - **주의**: 운영 DB가 하나라도 중간 리비전에 있으면 실패하므로, squash 전 전 환경 head 통일 필수.
- **검증**: 빈 DB에 새 베이스라인 1개로 `upgrade head` → `uv run pytest` 전체 그린 + 기존 DB에 stamp 절차 리허설.
- **예상 공수**: M

---

### [IX-5] 구조화 로깅·request-id 부재 — 운영 관측성 공백
- **우선순위 제안**: P2
- **카테고리**: 인프라
- **증거**: `backend/app/main.py:43-55` — `logging.basicConfig` 뿐. request-id 미들웨어/correlation 부재(LLM 트레이스는 Langfuse/LangSmith로 별도 존재하나 **HTTP 요청 레벨** 상관관계 없음). 에러 추적(Sentry류) 없음.
- **문제점**: 프로덕션에서 "이 500 에러가 어떤 요청/유저/대화에서 났나"를 로그로 역추적 불가. 멀티유저 서비스로 전환 완료(ADR-016)된 시점에서 운영 필수 요소.
- **리팩토링 방안**:
  1. request-id 미들웨어: `X-Request-ID` 수신 또는 uuid4 생성 → `contextvars`에 저장 → 응답 헤더 echo.
  2. logging Filter로 모든 로그 레코드에 request_id/user_id 주입, 포맷을 JSON lines로(prod만; dev는 현행 유지 플래그).
  3. 예외 핸들러에서 request_id 포함 구조화 에러 로그 + (선택) Sentry SDK 도입은 별도 결정.
  4. **주의**: 로그에 credential/토큰이 흐르지 않도록 기존 redaction 규칙(CLAUDE.md Backend redaction) 준수 — 특히 헤더 로깅 금지.
- **검증**: 두 동시 요청의 로그가 request_id로 구분되는지, 에러 응답 헤더에 id가 도는지 테스트.
- **예상 공수**: M

---

### [IX-6] aiosqlite↔PostgreSQL 격차 — integration 마커는 있는데 CI에서 안 돎
- **우선순위 제안**: P2
- **카테고리**: 테스트
- **증거**: `backend/pyproject.toml` — `markers = ["integration: tests requiring live Postgres (skipped by default via addopts)"]` + `addopts = "-m 'not integration'"`. 즉 live PG 테스트 체계는 설계돼 있으나 CI가 없어 **아무 데서도 정기 실행되지 않음**. 기본 스위트는 aiosqlite in-memory라 FK enforcement·JSONB 연산·`FOR UPDATE` 락 동작·부분 유니크 인덱스(SEC-3 방안) 등 PG 전용 버그 클래스를 놓침 (2026-07-03 감사의 "aiosqlite FK 미검증" 지적과 동일 맥락).
- **문제점**: 이 문서의 성능 리팩토링(인덱스, FOR UPDATE 제거, keyset)은 PG에서만 검증 의미가 있는데 검증 채널이 없음.
- **리팩토링 방안**:
  1. IX-1 CI에 `backend-integration` 잡 추가: `services: postgres:16-alpine` + `DATABASE_URL`/`DATABASE_URL_SYNC` 주입 + `uv run pytest -m integration`.
  2. integration 대상 테스트 확충: FK cascade(대화 삭제), 트리거 동시 실행(SEC-3), keyset 페이지네이션 경계, credential rotation 배치.
  3. 로컬 실행 절차를 CLAUDE.md 테스트 섹션에 한 줄 추가(`docker compose up -d postgres && uv run pytest -m integration`).
- **검증**: integration 잡이 PG 서비스로 그린, sqlite에서 통과하지만 PG에서 실패하는 사례(예: FK 위반) 재현 테스트 1개로 격차 커버 입증.
- **예상 공수**: S~M (CI 잡) + 테스트 확충은 점진

---

### [IX-7] e2e 스펙 69개 — captures 투어와 regression 혼재
- **우선순위 제안**: P3
- **카테고리**: 테스트
- **증거**: `frontend/e2e/` 루트에 regression 스펙과 `captures/` 디렉토리, `manual-atlassian-oauth.spec.ts`(수동 전용) 혼재. `playwright.config.ts`(48줄)에 프로젝트 분리 없음. 실행이 파일명 알파벳 순서에 의존하는 함정도 메모리에 기록돼 있음(chat-states 워밍업).
- **문제점**: 전체 실행 시 캡처 투어(스크린샷 목적, `E2E_CAPTURE_TOUR` 게이트)와 수동 스펙이 필터 없이 섞여 러너/CI 구성 시 매번 grep 필터를 손으로 짜야 함.
- **리팩토링 방안**:
  1. `playwright.config.ts`에 projects 분리: `regression`(기본, captures/·manual-* 제외 testIgnore), `captures`(testDir: e2e/captures, env 게이트 문서화), `manual`(수동 전용).
  2. `pnpm e2e`, `pnpm e2e:captures` 스크립트 추가.
  3. 알파벳 순서 의존(warm-up) 주석을 config에 명시.
- **검증**: `pnpm exec playwright test --project=regression`이 captures를 건드리지 않는지 확인.
- **예상 공수**: S

---


## 10. 하지 말 것 — 검토 후 기각된 항목

분석 과정에서 후보로 올랐으나 **Simplicity First 원칙에 따라 명시적으로 기각**한 항목. 나중에 누군가 다시 제안할 때 재검토 비용을 아끼기 위해 기록한다.

| 기각 항목 | 사유 |
|-----------|------|
| `config.py` Settings 도메인별 분리 (BE-S12) | 필드 101개지만 섹션 주석으로 구획 양호. 분리 시 `settings.X` 접근 경로 전면 변경 diff만 유발, 실익 없음 |
| 제네릭 BaseService (CRUD 공통 부모 클래스) | 각 서비스 create/update가 스케줄러 sync·검증·감사 등 도메인 로직 보유 — 제네릭화는 과추상화. 필요 시 `commit_refresh(db, obj)` 미니 헬퍼까지만 |
| 모든 리소스 단일 제네릭 `load_owned(Model, id, user)` | 소유권 의미(system 포함 여부, 404-collapse)가 리소스마다 달라 플래그 폭발. 리소스별 얇은 의존성 함수가 정답 (BE-D1/D4) |
| 프론트 제네릭 `useResourceQuery` 훅 | 현 API 3계층(`apiFetch` → `xxxApi` → `use-xxx`)은 idiomatic TanStack — 보일러플레이트가 아니라 관례 |
| credential 보간 로직 공통화 | `app/credentials/interpolation.py`의 `resolve_deep` 단일 함수로 **이미 완전 중앙화**(11 call sites). 조치 불필요 |
| chat_service의 scope/is_pinned 복합 커서를 공용 커서로 통일 | 도메인 특수 필드 — 무리한 통일은 오히려 결합 유발. 정규화 함수(`normalize_cursor_dt`)만 공유 (BE-D5) |
| BE-D8 Response 스키마 믹스인 전면 적용 | `user_id` nullable 여부가 리소스마다 달라 베이스에 못 넣음. `ORMModel`(config만)이 안전선이며 이득 최소 — P1/P2 소진 후에만 |
| 전면 OpenAPI codegen (orval 등) | API 레이어가 이미 깨끗 — **타입만** 생성하는 `openapi-typescript` 경량 도입이 적정선 (FE-S10) |

---

## 11. 신규 기능 발굴

### 11-A. 이미 계획·문서화된 로드맵 (재발굴 아님 — 착수만 하면 되는 것)

| 항목 | 출처 | 비고 |
|------|------|------|
| G1 멀티모달 입력 — 첨부 이미지가 모델에 전달 안 됨 | `docs/design-docs/chat-feature-gap-analysis.md` | **채팅 갭 1순위**. 끊김점이 `conversation_agent_protocol_commands.py` 한 곳으로 좁혀져 있고 `models.supports_vision` 컬럼 존재 — 공수 M. checkpoint base64 팽창만 리스크 |
| Marketplace MCP/Agent publish·install UX 확장 | `TASKS.md` Active Follow-ups | Skill Phase 1 완료 상태. install_service에 MCP/blueprint 경로는 이미 존재(BE-S3 분해와 함께 진행 권장) |
| 아티팩트/공유/마켓 설치/메모리 승인 E2E 확충 | `TASKS.md` | IX-7(프로젝트 분리)과 함께 |
| 멀티-worktree 스케줄러 하드닝 | `TASKS.md` | SEC-3(중복실행 가드)이 선행 조건 |
| OpenWiki Phase 2 — 스케줄 자동갱신 | 메모리(openwiki 분석) | 트리거 경로의 스킬 차단(`risk.py:317-325`) 해제 + invoke 경로 artifact recorder 주입 필요 |
| 스킬 빌더 멀티턴 대화 이관 + 스킬 관리 UI 대개편 | 메모리(skill-ui-overhaul) | 현 스킬 빌더는 single-shot 폼 — 옵션 A(shell 재사용)/B(deep-agent 재작성)/C(일반 에이전트화) 결정 필요 |
| 미들웨어 UX Phase D — 프리셋/실행 순서 DnD/provider 자동 감지 | `TASKS.md` Phase 14 잔여 | 레지스트리·UI 인프라 완비, 순수 UX 작업 |
| 채팅 소소한 quick win 군: G9 슬래시 커맨드, G11 CSV/컬럼 토글, G12 음성·draft, G15 시간치환, G10-C/D | chat-feature-gap-analysis | 전부 S급으로 feasibility 검증 완료 |

### 11-B. 신규 기능 제안 (우선순위순)

제안 전 관련 테이블/라우터/화면의 존재 여부를 grep으로 확인해 이미 있는 기능은 제외했다. (확인 예: 트리거 타입은 `interval|cron|one_time`뿐 — `models/agent_trigger.py:23-25`, notification 인프라 부재, RAG/벡터 인프라 부재, agent_blueprints·daily_spend_*·skill_evaluation_* 존재.)

### [F1] 웹훅(이벤트) 트리거
- **가치**: 지금은 시간 기반(interval/cron/one_time)으로만 에이전트가 깨어남. 외부 이벤트(폼 제출, 알림 수신, GitHub 이벤트, IoT)로 에이전트를 실행할 수 있으면 자동화 범위가 근본적으로 확장 — n8n/Zapier류 대비 최대 격차 중 하나.
- **우선순위 제안**: P1 (가치 높음 × 기존 자산으로 비용 낮음)
- **기존 자산 활용**: `agent_triggers`+`agent_trigger_runs` 테이블, `trigger_executor.py`(invoke 모드·HiTL 비활성 정책 그대로 재사용), Agent API의 키 인증 패턴(`agent_api_*`), `audit_events`.
- **구현 스케치**:
  1. 마이그레이션: `agent_triggers.trigger_type`에 `"webhook"` 추가 + `webhook_secret`(암호화 저장, Cipher V2 재사용) 컬럼.
  2. `POST /api/hooks/{trigger_id}` 공개 엔드포인트 — HMAC-SHA256 서명 검증(`X-Moldy-Signature`), rate limit, payload 크기 상한.
  3. payload를 트리거 메시지 템플릿에 보간해 `trigger_executor.execute_trigger` 호출(SEC-3의 중복실행 가드 선행 필수).
  4. 프론트: 스케줄 폼(`features/schedules/components/schedule-form.tsx`)에 웹훅 타입 추가 — URL/시크릿 표시 + 재발급 버튼.
  5. run 이력은 기존 `agent_trigger_runs` 화면 그대로.
- **예상 공수**: M

### [F2] RAG 지식베이스 (문서 업로드 → 검색 도구)
- **가치**: "내 문서를 아는 에이전트"는 노코드 에이전트 빌더의 표준 기대치(Dify/Flowise 모두 보유)인데 Moldy에는 스킬(지시문)만 있고 문서 KB가 없음. 사내 위키/매뉴얼 기반 Q&A 에이전트를 즉시 가능하게 함.
- **우선순위 제안**: P1 (가치 최대 — 공수는 L이지만 차별화 핵심)
- **기존 자산 활용**: PostgreSQL 16(+pgvector 확장), 업로드 파이프라인(`POST /api/uploads`·`message_attachments`), credential 시스템(임베딩용 LLM 키 — ADR-013 우선순위 그대로), tool registry(`builtin:*` 패턴), 문서 아티팩트 뷰어(프리뷰 재사용), APScheduler(백그라운드 인덱싱).
- **구현 스케치**:
  1. 마이그레이션: pgvector 확장 + `knowledge_bases`/`kb_documents`/`kb_chunks(embedding vector)` 테이블 (is_system/user_id 규약 준수).
  2. 인덱싱 파이프라인: 업로드 → 텍스트 추출(기존 문서 파서 자산) → 청킹 → 임베딩(system LLM settings에 embedding role 추가, ADR-019 패턴) — 스케줄러 백그라운드 잡으로 비동기 처리.
  3. `builtin:kb_search` 도구: 에이전트에 KB 연결(`agent_knowledge_bases` 링크 테이블), top-k 검색 결과에 출처 chunk 포함.
  4. 프론트: `/knowledge` 관리 화면(업로드·인덱싱 상태), 에이전트 설정에 KB 연결 섹션.
  5. 채팅 인용 카드: 기존 search-tool 리치카드(출처 집계) 패턴 재사용.
- **예상 공수**: L

### [F3] 사용량 쿼터·예산 제한
- **가치**: 멀티유저 전환(ADR-016) 완료 후 운영자에게 필수 — 특정 유저/에이전트의 비용 폭주를 막을 수단이 현재 없음(집계·표시만 있음).
- **우선순위 제안**: P2
- **기존 자산 활용**: **`daily_spend_*` 집계 테이블이 이미 존재**, `token_usages`, super_user 권한, 채팅 에러 버블(예산 초과 안내 재사용).
- **구현 스케치**:
  1. `user_budgets`(user_id, monthly_usd_cap, alert_threshold) 테이블 + super_user 관리 API.
  2. run 시작 전 체크: `agent_stream_runner` 진입점에서 daily_spend 합산 대비 캡 검사 → 초과 시 구조화 에러(error_codes 패턴)로 즉시 반환.
  3. 80%/100% 도달 시 알림(F4와 연계) + 컴포저 옆 예산 게이지(기존 컨텍스트 게이지 UI 패턴 재사용).
  4. super_user 화면: `/settings/usage`에 유저별 캡 관리 탭 추가.
- **예상 공수**: M

### [F4] 알림 센터 (G14 확장)
- **가치**: 트리거 실패·HITL 승인 대기·장시간 런 완료를 현재는 해당 화면에 들어가야만 알 수 있음. 스케줄 자동화가 늘수록(F1 도입 시 더욱) 미확인 실패가 조용히 쌓임.
- **우선순위 제안**: P2
- **기존 자산 활용**: `message_events` SSE 인프라, `agent_trigger_runs`(실패 상태), HITL interrupt 이벤트, Google Chat Webhook 도구(외부 채널 재사용), 네비게이터 레이아웃(벨 아이콘 배치).
- **구현 스케치**:
  1. `notifications` 테이블(user_id, type, payload, read_at) + 서비스/라우터.
  2. 발생점 훅 3곳: trigger_executor 실패 시, HITL interrupt 발생 시(트리거 아닌 대화만), 60초+ 런 완료 시.
  3. 프론트: 헤더 벨 아이콘 + 미읽음 배지 + 드롭다운 목록(클릭 시 해당 대화/스케줄로 점프 — 기존 jump-to-message 재사용).
  4. (선택) 유저별 외부 채널 설정: Google Chat webhook/이메일로 포워딩.
- **예상 공수**: M

### [F5] 에이전트 버전 히스토리·롤백
- **가치**: 프롬프트/도구 구성을 실험하다 "어제 잘 되던 설정"으로 못 돌아감. Fix Agent·Assistant가 에이전트를 자동 수정하는 제품 특성상 변경 이력의 가치가 특히 큼.
- **우선순위 제안**: P2
- **기존 자산 활용**: **`agent_blueprints`가 이미 에이전트 스냅샷 포맷** (marketplace 설치용 — `models/agent_blueprint.py`), `install_service._apply_agent_payload_to_blueprint` 역변환 로직, `audit_events`(변경 주체 기록).
- **구현 스케치**:
  1. agent_service.update 시 변경 전 상태를 blueprint 포맷으로 `agent_versions` 테이블에 스냅샷(직전과 diff 없으면 스킵).
  2. `GET /api/agents/{id}/versions` + 버전 간 diff API(system_prompt는 텍스트 diff, 도구/스킬은 집합 diff).
  3. 롤백: blueprint→agent 적용 로직(BE-S3 분해로 모듈화된 `install/agent_blueprint.py` 재사용).
  4. 프론트: 에이전트 설정에 "버전 기록" 탭 — 타임라인 + diff 뷰 + 롤백 버튼(확인 다이얼로그).
- **예상 공수**: M

### [F6] 에이전트 평가(eval) 하네스
- **가치**: 프롬프트/모델 변경이 품질을 올렸는지 내렸는지 확인할 방법이 현재 없음(감으로 판단). F5(버전)와 결합하면 "버전 A vs B 성적표"가 가능.
- **우선순위 제안**: P2 (F5 이후)
- **기존 자산 활용**: **`skill_evaluation_*` 서비스 20+ 파일**(스킬 평가 인프라·`SKILL_EVALUATION_ENABLED` 플래그 — 평가 개념이 이미 코드베이스에 존재), `e2e_scripted_model`, `trigger_executor` invoke 모드(배치 실행), checkpoint fork(재실행 인프라), `message_feedback`(암묵 평가 데이터).
- **구현 스케치**:
  1. `eval_datasets`/`eval_cases`(질문, 기대 기준) CRUD.
  2. 배치 러너: 에이전트(또는 버전)에 대해 케이스 일괄 invoke — trigger_executor 패턴 재사용, 동시성 제한.
  3. LLM-judge 채점(system LLM settings에 judge role) + 점수 저장.
  4. 프론트: 에이전트 설정 "평가" 탭 — 데이터셋 관리, 실행, 버전 간 점수 비교 표.
- **예상 공수**: L

### [F7] 에이전트 임베드 위젯
- **가치**: 만든 에이전트를 자기 웹사이트에 채팅 위젯으로 붙이는 것 — Agent API(M56)가 이미 완성돼 있어 마지막 한 조각(위젯 JS)만 없음. 외부 노출 = 제품 홍보 루프.
- **우선순위 제안**: P2
- **기존 자산 활용**: **Agent API 완비**(`agent_deployments`, scoped API keys, threads, runs, `/v1` 스트리밍), `share_links`(공개 노출 패턴), 디자인 토큰.
- **구현 스케치**:
  1. 경량 위젯 번들(iframe 방식 — 호스트 CSS 격리): 플로팅 버튼 + 채팅 패널, `/v1` 스트리밍 소비.
  2. deployment 설정 확장: 위젯 테마 색/인사말/허용 도메인(Origin 검증).
  3. 퍼블릭 rate limit(deployment 단위) + 위젯 전용 익명 thread 정책.
  4. Agent API 설정 화면에 embed `<script>` 스니펫 복사 UI.
- **예상 공수**: M

### [F8] 대화 분석 대시보드
- **가치**: 운영자·헤비유저가 "어떤 에이전트가 많이 쓰이고, 어디서 실패하며, 비용이 어디로 가는지"를 한눈에. 현 `/usage`는 토큰 집계 위주.
- **우선순위 제안**: P3
- **기존 자산 활용**: `token_usages`, `daily_spend_*`, `agent_trigger_runs`, `message_feedback`, `audit_events`, 기존 수제 SVG 차트 컴포넌트(chart-card — FE-D4 토큰화 이후).
- **구현 스케치**:
  1. 집계 API: 기간별 대화 수/토큰/비용, 에이전트별 top-N, 도구 사용 빈도, 실패율(error_message 존재 run), 피드백 분포.
  2. `/usage`를 탭 구조로 확장(사용량 | 분석) 또는 `/analytics` 신설.
  3. BE-P6 인덱스(특히 token_usages) 선행 필수 — 집계 쿼리가 풀스캔이면 역효과.
- **예상 공수**: M

### [F9] 대화 폴더·태그 정리
- **가치**: 대화가 수백 개 쌓이면 pin/검색만으로 부족. 프로젝트별 그룹핑 요구는 채팅 제품의 공통 진화 경로.
- **우선순위 제안**: P3
- **기존 자산 활용**: 네비게이터(keyset 페이지네이션·pin·rename 완비, M63 인덱스), `conversations` 테이블.
- **구현 스케치**: ① `conversation_tags`(또는 folders) 테이블 + CRUD ② 네비게이터 필터 칩 ③ 대화 컨텍스트 메뉴에 태그 지정. 
- **예상 공수**: S~M

### [F10] 팀 워크스페이스 (에이전트 협업 공유)
- **가치**: 현재 공유는 marketplace 발행(비동기 복사) 또는 대화 share link뿐 — 팀이 **같은 에이전트 인스턴스**를 함께 운영·수정하는 모델이 없음. B2B 방향의 관문 기능.
- **우선순위 제안**: P3 (가치 크지만 권한 모델 전면 확장이라 공수 최대 — ADR 선행 필수)
- **기존 자산 활용**: marketplace ACL 테이블(공유 권한 개념), `is_super_user` 권한 모델(RBAC 확장 여지가 ADR-016에 명시), audit_events.
- **구현 스케치**: ① ADR 작성(workspace vs 리소스별 공유 — 스코프 결정이 핵심) ② `workspaces`/`workspace_members`(role) ③ 리소스 소유를 user_id → owner(workspace|user) 다형으로 확장(대규모 마이그레이션) ④ 초대 플로우 + 멤버 관리 UI. **주의: F1~F9 대비 리스크가 한 차원 높으므로 별도 스펙(/spec) 트랙 권장.**
- **예상 공수**: XL

### 기능 우선순위 요약

| 순위 | 기능 | 근거 |
|:---:|------|------|
| 1 | G1 멀티모달 입력 (기계획) | 끊김점 1곳, 컬럼 존재 — 최소 공수로 채팅 체감 최대 |
| 2 | F1 웹훅 트리거 | 자동화 확장의 관문, 기존 트리거 인프라 재사용 |
| 3 | F2 RAG 지식베이스 | 경쟁 제품 대비 최대 격차, 차별화 핵심 |
| 4 | F3 쿼터·예산 | 멀티유저 운영 필수, daily_spend 재사용으로 공수 M |
| 5 | F4 알림 센터 | F1 도입 시 필수성 급증 |
| 6 | F5 버전 히스토리 → F6 평가 | blueprint/skill_evaluation 자산 재사용, 순서 의존 |
| 7 | F7 임베드 위젯 | Agent API 마지막 한 조각 |
| 8+ | F8 분석, F9 태그, F10 워크스페이스 | 여유 시 / F10은 ADR 선행 |

---

## 12. 부록 — 공통 검증 커맨드

모든 리팩토링 PR은 병합 전 아래를 통과해야 한다 (IX-1 CI 도입 후에는 자동화).

```bash
# 백엔드 (backend/)
uv run ruff check .                          # 린트
uv run pyright                               # 타입체크 — 전체 968 기존 에러: 수정 파일 단위로만 게이트, CI는 non-blocking
uv run --with pytest-xdist pytest -q -n 4    # 전체 테스트 (aiosqlite, DB 불필요)
uv run pytest -m integration                 # live PG 필요 시 (docker compose up -d postgres 선행)

# 프론트엔드 (frontend/)
pnpm lint                                    # eslint + design-system/a11y/i18n/architecture 가드
pnpm vitest run                              # 전체 유닛 (개별 파일 아님 — CLAUDE.md 규칙)
pnpm build                                   # tsc + Next 빌드

# 채팅 관련 변경 시 추가
pnpm exec playwright test e2e/chat-langgraph-v3-regressions.spec.ts
# 전체 e2e는 throwaway 스택 절차(CLAUDE.md "E2E 포트/DB 격리") 준수
# 푸시 전 backend pytest는 SKILL_EVALUATION_ENABLED=true 필요
```

**성능 항목 측정 도구**: SQLAlchemy `echo=True` 또는 pytest 쿼리 카운터(N+1 검증), `py-spy`(이벤트 루프/CPU 프로파일), `EXPLAIN ANALYZE`(인덱스), React DevTools Profiler(리렌더 커밋 수), Network 탭(폴링 횟수), Lighthouse(TBT/번들).

**문서 유지 규칙**: 항목 완료 시 이 문서의 해당 행에 취소선 + PR 번호를 남기고, 라인 번호가 크게 어긋난 항목은 심볼명으로 재탐색해 갱신한다.
