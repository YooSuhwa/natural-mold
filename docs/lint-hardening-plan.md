# 린트·정적분석 하드닝 계획

> 작성 2026-07-08. 리팩토링 마스터 플랜(`docs/refactoring-plan-2026-07.md`)의 DevX 후속 트랙.
> 목적: "이렇게 개발했는데 lint가 잡아줬으면" 싶은 것들을 자동 게이트로 만든다. 바이브코딩으로 빠르게 쌓인 컨벤션 드리프트를 사람의 기억이 아니라 도구로 막는다.
> 모든 수치는 2026-07-08 main 기준 실측(`ruff check --select <RULE> --output-format concise app/`, grep 카운트). ruff 버전은 uv.lock 고정이라 결정론적.

## 0. 핵심 진단

이 코드베이스의 린팅은 **프론트엔드는 성숙, 백엔드는 빈약**한 비대칭 구조이고, 결정적으로 **프론트의 성숙한 커스텀 가드가 자동 파이프라인에 연결돼 있지 않다**.

| | 프론트엔드 | 백엔드 |
|--|-----------|--------|
| 규모 | ~100k 줄 | ~84k 줄 |
| 기본 린터 | eslint (flat config) | ruff (E/F/W/I/UP/B/SIM/ASYNC) |
| 커스텀 가드 | **6개**(design-system, static-i18n, frontend-architecture, jsx-a11y, type-safety, e2e-hygiene) | **0개** |
| 보안 민감도 | 중 | **높음**(JWT 인증·크리덴셜 암복호·소유권) |

가장 큰 문제는 **B(커스텀 가드 미연결)**와 **C(보안 룰 off)**다. 나머지는 저비용 개선.

---

## A. 커스텀 가드가 CI·pre-commit 어디에도 연결 안 됨 — 🔴 P0

> **A-1 ✅ 완료 (2026-07-10, PR #287)**: 재측정 후 정당한 예외 3건 등록(i18n `global-error.tsx` SKIP / type-safety 테스트 한정 이유-주석 `@ts-expect-error` 허용 / e2e-hygiene `e2e/captures/` fixed-timeout만 면제) + 예외별 네거티브 회귀 테스트(`tests/unit/lint/guard-exemptions.test.ts`, i18n 테스트에 형제 파일 단언) → **그린 가드 4개(lint·i18n·type-safety·e2e-hygiene)**를 CI 개별 스텝 + lint-staged(`frontend/**/*.{ts,tsx}`)에 연결.
> **⚠️ 재분류 (PR #287 2차 리뷰)**: `lint:frontend-architecture`는 아래 실측 표의 ✅가 **거짓 그린** — 비-strict 모드는 위반 48건에도 **항상 exit 0**(게이트 값 없음)이고, 강제인 `--strict`는 **blocking 3건으로 레드**. 따라서 A-2로 이동: strict blocking 해소(또는 strictBaseline 검토 등록) 후 `lint:frontend-architecture:strict`를 CI에 연결. 재현: `node scripts/check-frontend-architecture.mjs --strict; echo $?`.
> **A-2 잔여**: a11y(신규 4 + baseline 해소 2)·design-system(12 + 카드 경고 19)은 실제 컴포넌트 수정(FE-D2·FE-D4 연동), frontend-architecture는 strict blocking 3건 수정 후 연결. 전부 그린이 되면 CI를 `lint:all` 호출로 교체(단, lint:all의 frontend-architecture도 strict로 교체 필요).
> **lint-staged 주의**: 가드는 staged 파일 인자를 무시하고 트리 전체를 스캔한다 — untracked 위반 파일이 있으면 무관한 커밋도 막힐 수 있다(CI가 백스톱이므로 fail-open은 아님). 기존 `frontend/src/**` 엔트리(prettier/eslint --fix)와 병렬 실행되므로 드문 read-write race로 flaky 실패가 가능 — 반복되면 `.husky/pre-commit`을 `npx lint-staged --concurrent false`로.

- **증거**:
  - `frontend/scripts/`에 커스텀 가드 6개 존재(`check-static-i18n.mjs`가 한국어/영어 메시지 정합을 검사하는 바로 그 스크립트).
  - `.github/workflows/ci.yml` frontend 잡: `pnpm lint`(=`eslint`) + `vitest run` + `build`. 커스텀 가드 호출 **0회**.
  - `package.json` lint-staged: `prettier --write` + `eslint --fix`. 커스텀 가드 호출 **0회**.
  - 즉 `pnpm lint:i18n`, `pnpm lint:design-system` 등은 **개발자가 손으로 기억해서** 돌려야만 실행됨. AGENTS.md에 "새 화면 작업 후 돌려라"는 안내는 있으나 강제 아님.
- **문제**: "i18n 한국어/영어 정합이 안 맞으면 lint 오류"라는 기대가 스크립트로 구현돼 있는데도, 자동으로 걸리지 않는다. 실제로 이번 세션의 FE-D1 작업 때 `pnpm lint`(eslint)만 돌렸고, i18n 정합을 깼더라도 CI가 못 잡았을 것.
- **조치**:
  1. `frontend/package.json`에 집계 스크립트 추가:
     ```json
     "lint:all": "pnpm lint && pnpm lint:i18n && pnpm lint:design-system && pnpm lint:frontend-architecture && pnpm lint:a11y && pnpm lint:type-safety && pnpm lint:e2e-hygiene"
     ```
  2. CI frontend 잡의 `pnpm lint`를 `pnpm lint:all`로 교체(또는 각 가드를 개별 스텝으로 — 실패 지점이 명확).
  3. lint-staged에 변경 파일 대상 가드 추가(전체 스캔이 무거우면 `check-static-i18n.mjs`처럼 빠른 것만 staged, 나머지는 CI 전담).
  4. **주의**: 6개 가드가 현재 그린인지 먼저 확인(`pnpm lint:*` 각각). baseline 경고가 있는 가드(jsx-a11y는 `jsx-a11y-baseline.json`)는 baseline 초과만 실패하도록 이미 설계됨 — 그대로 CI에 넣으면 됨.
- **검증**: 의도적으로 i18n 키를 한쪽만 추가한 커밋이 CI에서 빨간불이 되는지.
- **공수**: ~~S~~ → **M** (아래 실측으로 상향)

### A 실측 (2026-07-08) — 연결 전 트리아지가 선행 필요

각 가드를 개별 실행한 결과, **6개 중 4개가 이미 위반 상태**다(강제 안 한 결과 위반이 축적됨 — A가 필요한 이유의 실증). 주의: `pnpm run <g> | tail`의 종료코드는 tail의 것이라 항상 0으로 보인다(pyright 백로그 때와 동일 함정) — 반드시 `pnpm run <g> >/dev/null 2>&1; echo $?`로 확인.

| 가드 | 상태 | 위반 | 트리아지 판단 |
|------|------|:---:|---------------|
| `lint` (eslint) | ✅ | — | — |
| `lint:frontend-architecture` | ⚠️ 거짓 그린 | strict 3 | 비-strict는 항상 exit 0(게이트 아님), 강제 모드는 `--strict`뿐 → strict blocking 3건 해소 후 strict를 연결 (위 재분류 노트) |
| `lint:i18n` | ❌ | 3 | 전부 `global-error.tsx`(FE-D1에서 신규) — i18n 프로바이더 밖이라 정적 영문 불가피 → **가드 SKIP_FILE_PATTERNS에 예외 등록** |
| `lint:type-safety` | ❌ | 2 | `chat-route-replacement.test.ts`의 `@ts-expect-error`(SSR window 제거 시뮬) — 정당 → **테스트 예외 또는 이유-주석 허용** |
| `lint:e2e-hygiene` | ❌ | 12 | 전부 `e2e/captures/`의 `waitForTimeout`(스크린샷 투어라 고정 대기 실용적) → **captures 디렉토리 예외 또는 대기 조건화** |
| `lint:a11y` | ❌ | 신규 3 + baseline 해소 2 | approval-card/artifact-panel 컨트롤 라벨 — **실제 수정**(FE-D2와 연동) + baseline 갱신 |
| `lint:design-system` | ❌ | 팔레트/svg/arbitrary 다수 + card 경고 18 | data-ui(chart/stats/terminal-card)의 `text-emerald-*`·inline-svg(FE-D4), message-attachments/approval-card arbitrary-layout — **실제 토큰화 수정 또는 문서화된 예외 등록** |

**착수 방식**: (1) 정당한 예외 3개(i18n/type-safety/e2e-hygiene)를 각 가드에 등록해 그린화 → 그 3개를 먼저 CI 연결. (2) a11y·design-system은 실제 컴포넌트 수정(FE-D2·FE-D4와 연동)이라 별도 커밋/PR로 그린화 후 연결. **한 번에 6개를 CI에 넣지 말 것** — 빨간 가드를 CI에 넣으면 이후 모든 PR이 막힌다. `frontend/package.json`에 `lint:all` 집계 스크립트는 미리 추가해 뒀다(가드가 다 그린이 된 뒤 CI가 이걸 호출).

---

## B. 백엔드 커스텀 가드 부재 — 🟠 P1

프론트의 `check-*.mjs` 패턴을 백엔드에도 도입한다. grep 기반 경량 스크립트(`backend/scripts/check_*.py`) + CI 스텝.

### B-1. raw HTTPException 금지 (error_codes 팩토리 강제)
- **증거**: `app/routers/`에 `raise HTTPException` **38곳**. BE-D2(#281)에서 404/403 21곳을 손으로 error_codes 팩토리로 바꿨는데, 규칙이 있었으면 애초에 리뷰에서 자동 검출.
- **규칙**: 라우터에서 `raise HTTPException(` 직접 사용 금지 → `app/error_codes.py` 팩토리 사용. 예외(파일별 allowlist)는 명시.
- **효과**: 응답 스키마(`{error:{code,message}}`) 일관성, enumeration-oracle 계약 준수.

### B-2. 함수-로컬 `app.*` import 감지 (순환 결합 냄새)
- **증거**: 함수 본문 안에서 `from app.` / `import app.` **155곳**. 대부분 services↔agent_runtime 양방향 결합(BE-S4)을 피하려는 지연 import — "숨은 런타임 의존"이라 정적 분석·IDE 탐색을 무력화.
- **규칙**: 신규 함수-로컬 import 증가를 baseline 카운트로 막는다(줄이는 건 OK, 늘리는 건 실패). 근본 해결은 BE-S4.

### B-3. `print()` 금지 (ruff `T20`으로 충분)
- **증거**: `app/`에 `print(` **8곳**. 프로덕션은 `logging` 사용.
- **규칙**: ruff `select`에 `T20` 추가(커스텀 불필요).

### B-4. 라우터 직접 `db.commit()` — 관찰용(경고)
- **증거**: `app/routers/`에 `await db.commit()` **152곳**. 트랜잭션 경계가 라우터에 흩어져 있음. 전면 금지는 과함(현 아키텍처가 라우터 commit 관례) → 카운트 추적만.
- **공수**: B-1/B-2 = M(스크립트+baseline), B-3 = S, B-4 = S(관찰).

---

## C. ruff 보안 룰(S) off — SSRF를 자동 검출 가능 — 🟠 P1

- **증거**: `--select S`로 **43건**. 내역:

  | 룰 | 건수 | 의미 |
  |----|:---:|------|
  | **S310** | 2 | **URL open (SSRF)** — SEC-1에서 손으로 찾은 web_scraper 취약점을 ruff가 자동 검출 |
  | S603/S607 | 14 | subprocess 실행(skill_executor — 보안 민감 경로) |
  | S105/S106 | 13 | 하드코딩 비밀번호/시크릿(상당수 상수명 오탐 — 검토 후 ignore) |
  | S101 | 10 | 프로덕션 `assert`(최적화 빌드에서 제거 → 검증 우회) |
  | S110 | 2 | try-except-pass(조용한 예외 삼킴) |
  | S311 | 1 | 약한 random |
  | S104 | 1 | 0.0.0.0 바인딩 |

- **문제**: JWT 인증·크리덴셜 암복호를 다루는 프로젝트에서 보안 린터가 꺼져 있음. SEC-1 SSRF는 S310으로 조기 발견됐을 것.
- **조치**:
  1. `[tool.ruff.lint] select`에 `"S"` 추가.
  2. 43건 중 진짜 위험(S310, S603/607의 미검증 입력, S101 프로덕션 assert)은 수정, 오탐(테스트의 assert=S101, 상수명 오탐=S105)은 `per-file-ignores` 또는 인라인 `# noqa: S105 — 상수명, 시크릿 아님`으로 이유와 함께 정리.
  3. 테스트 디렉토리는 `"tests/*" = ["S101"]`로 assert 허용.
- **공수**: M (43건 트리아지)

---

## D. 타입 안전성 게이트 부재 — 🟠 P1

- **증거**: pyright `basic` + CI `backend-typecheck` 잡이 `|| true`(non-blocking, 968 백로그 때문). `--select ANN`(어노테이션 강제)은 **477건**. CLAUDE.md는 "타입 힌트 필수" 컨벤션을 명시하나 강제 도구 없음.
- **조치**(순서):
  1. `docs/pyright-burndown-plan.md`의 B/C/D 단계 진행 → 968→0.
  2. 0 도달 후 CI `|| true` 제거(하드 게이트).
  3. 그 다음 `typeCheckingMode = "standard"` 승격 검토.
  4. `ANN`은 신규 코드부터 점진(`per-file-ignores`로 기존 파일 baseline, 신규 파일만 강제)하거나, 함수 시그니처 위주(`ANN001`/`ANN201`)만 우선.
- **공수**: L (번다운과 연동)

---

## E. 저노이즈 ruff 룰 배치 추가 — 🟡 P2

지금 켜도 부담 적은 것들(합계 ~66건). 한 PR에 묶어 트리아지.

| 룰 | 건수 | 효과 |
|----|:---:|------|
| `DTZ` | 1 | naive datetime 규약 명시(프로젝트 UTC-naive 정책과 정합 — 예외는 ignore) |
| `C4` | 3 | 불필요한 comprehension |
| `SLF` | 5 | private 멤버 외부 접근(`obj._x`) |
| `RET` | 9 | return 안티패턴(불필요한 else 등) |
| `PTH` | 10 | `os.path` → `pathlib` |
| `PT` | 19 | pytest 스타일(fixture/parametrize 일관성) |
| `N` | 19 | PEP8 네이밍 |

점진 도입(양 많음): `TRY`(372), `EM`(388), `PLR`(274), `RUF`(155) — 유용하나 별도 트랙.

- **공수**: S~M

---

## F. 억제(suppression) 부채 가시화 — 🟡 P2

- **증거**: `# noqa` **109건**, `# type: ignore` **76건**. 억제 자체는 정상이나 이유 없이 늘어남.
- **조치**:
  1. ruff `PGH` 룰(`PGH004` bare-noqa 금지 등) 추가 → 모든 noqa에 코드+이유 강제.
  2. pyright: `# type: ignore`에 이유 주석 컨벤션(도구 강제는 어려움 — 리뷰 체크리스트).
  3. (선택) noqa/type:ignore 개수 상한 스크립트(baseline 초과 실패).
- **공수**: S

---

## G. integration 테스트 마커 미강제 — 🟡 P2 (이번 CI 실패의 근본)

- **증거**: PR #280·#282 CI가 `test_conversation_run_lifecycle`·`test_stream_resume`의 xdist 스타베이션으로 실패. 원인은 이 파일들에 `@pytest.mark.integration`이 **없어서**(`test_m9_pg_roundtrip`만 마커 보유) 병렬 스위트에 섞인 것. CI 스텝 분리(`--ignore=tests/integration` + 직렬)로 급한 불은 껐음.
- **조치**: `tests/integration/conftest.py`에 자동 마커 훅으로 원천 차단:
  ```python
  def pytest_collection_modifyitems(items):
      for item in items:
          if "tests/integration/" in str(item.fspath):
              item.add_marker(pytest.mark.integration)
  ```
  단, 현재 CI는 `--ignore=tests/integration`로 통합을 통째 제외하고 직렬 스텝에서 별도 실행 중이므로, 마커 자동부여 시 직렬 스텝의 `addopts '-m not integration'`과 상호작용 확인 필요(직렬 스텝은 `-m ''` 또는 `-m 'integration or not integration'`로 실행하도록 조정).
- **공수**: S

---

## H. 기타 관찰 (참고)

- **DTO/스키마 검증**: Pydantic이 런타임 검증하나, 응답 스키마와 ORM 필드 드리프트를 잡는 정적 도구 없음(프론트 FE-S10의 openapi-typescript와 대칭 문제).
- **frontend `no-console`/`no-explicit-any`**: eslint flat config에 `no-console` 룰 없음(0건 매치). `check-type-safety.mjs`가 any를 커스텀 검사 중이나 이 역시 A의 미연결 대상.
- **커밋 메시지 규약**: CLAUDE.md에 `<type>(<scope>): <subject>` 규약 있으나 commitlint 등 강제 없음(선택).

---

## 권장 실행 순서

1. **A** — 커스텀 가드 CI·pre-commit 연결 (자산 존재, 비용 0, 즉효). 사용자가 원한 "i18n 자동 체크"가 바로 켜짐.
2. **C** — ruff `S`(보안) 켜기 + 43건 트리아지. 보안 프로젝트 필수.
3. **E** — 저노이즈 룰 배치(`DTZ,C4,SLF,RET,PTH,PT,N`) + `T20`(B-3).
4. **B-1** — 백엔드 `check_router_errors.py`(raw HTTPException 금지).
5. **G** — integration 마커 자동부여.
6. **F** — 억제 부채 가시화(`PGH`).
7. **D** — pyright 하드 게이트(968 번다운 완료 후).

각 항목은 독립 PR. 룰 추가 PR은 "룰 켜기 + 위반 트리아지"를 한 커밋에 담아 CI가 그린이 되게 한다(빨간 룰을 남기지 않는다).

## 검증 커맨드 (근거 재현)

```bash
cd backend
for R in S ANN DTZ PT TID RET TRY EM PTH RUF C4 N SLF PLR; do \
  echo "$R: $(uv run ruff check --select $R --output-format concise app/ | grep -cE ':[0-9]+:[0-9]+:')"; done
grep -rn "raise HTTPException" app/routers/ | wc -l          # B-1
grep -rnE "^    (from|import) app\." app/ | wc -l            # B-2
grep -rn "# noqa\|# type: ignore" app/ | wc -l              # F
```
