# pyright 백로그 번다운 계획

> 2026-07-08 기준. `docs/refactoring-plan-2026-07.md` IX-1의 후속 트랙.
> 목표: `uv run pyright` 0 에러 도달 → CI `backend-typecheck` 잡의 `|| true` 제거(하드 게이트 승격).
> 분석 데이터: `uv run pyright --outputjson` (pyright 1.1.408 — uv.lock이 버전을 고정하므로 CI/로컬 카운트는 결정론적).

## 0. 전체 분포 (분석 시점: 970건)

| 영역 | 건수 | 성격 |
|------|-----:|------|
| `data/**` | 343 | **앱 코드 아님** — 설치된 스킬 패키지 스크립트/업스트림 벤더 코드(런타임 데이터, 머신마다 다름) |
| `tests/**` | 502 | 소수 반복 패턴에 집중 (아래 §3) |
| `app/**` | 125 | 실코드 — 파일별로 국소화됨 (아래 §2) |

## Phase A — 설정 정리 ✅ 완료 (이 커밋)

`[tool.pyright]`에 `exclude = ["data", ".venv", "alembic/versions"]` 추가.
`data/`는 런타임 콘텐츠(설치 스킬·업로드·마켓 스냅샷)라 타입 게이트 대상이 아니며, 머신마다 내용이 달라 카운트를 비결정적으로 만들던 원인.

**결과: 970 → 627** (-343, 무위험)

## Phase B — app 실코드 125건 (예상 공수 M, 2~3 PR)

파일별 집중도가 높아 상위 8개 파일만 처리해도 77건이 사라진다.

| 파일 | 건수 | 지배적 원인 | 수정 방향 |
|------|-----:|-------------|-----------|
| `services/agent_blueprint_service.py` | 20 | JSON 컬럼(`dict \| None`)에 `.get()`/이터레이션 — null 가드 부재 | 함수 진입부에서 `payload = blueprint.payload or {}` 정규화 또는 로컬 내로잉. **주의: 실런타임 null 가능성 검토 — 단순 타입 침묵이 아니라 실제 방어가 맞는지 케이스별 판단** |
| `agent_runtime/legacy_event_projection.py` | 12 | `dict[bytes, bytes]`를 `dict[str, Any]` 파라미터에 전달 등 | "legacy" 모듈 — **삭제/사용처 확인 먼저** (dead면 제거가 정답). 살아있으면 디코딩 경계에 명시적 변환 |
| `agent_runtime/checkpointer.py` | 7 | 라이브러리(psycopg/langgraph) 타입 경계 | 경계에 좁은 `cast`/어댑터 |
| `agent_runtime/skill_builder/graph.py` | 7 | LangGraph state dict 접근 | TypedDict state 스키마 정의 |
| `agent_runtime/skill_builder/trigger_eval.py` | 7 | 〃 | 〃 |
| `services/conversation_run_worker.py` | 6 | Optional 접근 | null 가드 |
| `marketplace/install_service.py` | 5 | Optional/arg 타입 | BE-S3 분해 작업과 함께 처리 권장 |
| `agent_runtime/langgraph_pending_inputs.py` 외 꼬리 17파일 | 61 | 파일당 1~3건 | 기계적 개별 수정 |

- 진행 방식: 도메인별 소PR (blueprint/skill_builder/runtime 경계/꼬리 일괄). 각 PR에서 `uv run pyright <파일>` 0 확인.
- `app/seed/system_skill_packages/*/scripts` 4건은 subprocess 실행 스크립트(임포트 안 됨) — 수정 또는 exclude 추가 중 택1(수정 권장, 4건뿐).

## Phase C — tests 502건 (예상 공수 M~L, 패턴 기반)

메시지 패턴이 6개로 수렴하므로 파일이 아니라 **패턴 단위**로 친다:

| 패턴 | 건수 | 원인 | 수정 방향 |
|------|-----:|------|-----------|
| `"__getitem__" not defined on int/float/bool` + `No overloads for __getitem__` | ~218 | skill_evaluation 테스트들이 JSON 결과를 `dict[str, str\|int\|float\|bool]` 류 유니온으로 추론되는 헬퍼에서 받아 중첩 인덱싱 | 테스트 헬퍼 반환 타입을 `dict[str, Any]`로 명시(경계 1곳 수정으로 파일당 수십 건 소멸). 대상: `test_skill_evaluation_*` 6파일, `test_skill_builder_*` 3파일 |
| `Object of type "None" is not subscriptable` | 59 | Optional 반환 헬퍼를 바로 인덱싱 | 헬퍼에서 `assert x is not None` 후 반환 or 반환 타입 비-Optional화 |
| `No parameter named "model"` | 41 | `test_e2e_scripted_model.py` 단일 파일 — 생성자 kwargs가 타입에 없음 | 해당 팩토리 시그니처에 파라미터 명시(1곳) |
| `Cannot access attribute "coroutine" for BaseTool` | 24 | 빌더가 `BaseTool` 반환인데 테스트가 `StructuredTool.coroutine` 접근 | 공용 테스트 헬퍼 `def tool_coroutine(t): assert isinstance(t, StructuredTool); return t.coroutine` 도입 |
| TypedDict/`metadata` 키 접근 계열 | ~30 | LangChain 메시지 TypedDict 좁히기 실패 | `cast` 또는 `.get()` 전환 |
| 나머지 개별 | ~130 | 산발 | 파일별 기계 수정 |

- **원칙: 룰 완화(executionEnvironments로 tests만 끄기)는 최후 수단** — 위 패턴 수정은 대부분 헬퍼 시그니처 몇 줄이라 완화보다 싸고, `reportIndexIssue`류는 테스트에서도 실버그를 잡는다.

## Phase D — 게이트 승격 (S)

627 → 0 도달 시:
1. `.github/workflows/ci.yml`의 `backend-typecheck` 스텝에서 `|| true` 제거, 잡을 required로.
2. `docs/refactoring-plan-2026-07.md` IX-1 항목 갱신.
3. (선택, 별도 결정) `typeCheckingMode = "standard"` 상향 검토 — basic 0 달성 후에만.

## 진행 추적

| Phase | 목표 잔여 | 상태 |
|-------|----------:|------|
| A. data 제외 | 627 | ✅ 2026-07-08 (PR #279) |
| B. app 실코드 | ~502 | ⬜ |
| C. tests 패턴 | 0 | ⬜ |
| D. 하드 게이트 | 0 유지 | ⬜ |

검증 커맨드: `cd backend && uv run pyright | tail -1` (전체), `uv run pyright <파일>` (PR 단위).
