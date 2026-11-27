# 일반 채팅의 AG-UI 전환 검수

같은 Git 커밋에서 `langgraph_v3`와 `ag_ui` 프런트엔드를 각각 빌드해 기존 대화를 양쪽에서 열고 이어 쓴다. 실행기는 PostgreSQL 16, OpenFGA, FastAPI 두 프로세스와 작업 워커 두 프로세스를 로컬에 띄운다. 운영자와 다른 테넌트의 일반 사용자가 각각 제품 로그인으로 세션을 얻는다. 두 API에서 일반 사용자의 소속 조직 조회는 200, 다른 조직 및 존재하지 않는 조직 조회는 같은 404여야 한다. OpenFGA는 `organization,agent`에 적용하며 두 API는 행 수준 보안(RLS)이 켜진 일반 DB 역할로 접속한다.

### 준비

저장소 루트에서 실행한다. Python 3.12와 uv, **PATH에서 실행되는 Node.js 22**, pnpm 9.15.9, PostgreSQL 16 실행 파일, Unix 소켓으로 접근할 수 있는 로컬 Docker가 필요하다. PostgreSQL이 자동 검출되지 않으면 `MOLDY_PG16_BINDIR`를 `initdb`가 있는 디렉터리로 지정한다. 다음은 의존성과 브라우저를 처음 준비하는 명령이다.

```sh
node --version  # v22.x, nvm 사용자라면 nvm use 22
uv sync --project backend --locked
pnpm install --frozen-lockfile
pnpm --dir frontend exec playwright install chromium
docker pull openfga/openfga:v1.22.0
```

제품 소스는 `--source`의 커밋을 `git archive`로 읽는다. 기본값은 실행기가 있는 저장소의 `HEAD`다. 다른 체크아웃을 사용할 때는 `--source`, `--revision`, `--deps`를 지정하고 해당 소스의 lockfile로 설치한 Python과 Node 의존성을 사용한다. 실제 서비스의 DB나 계정, LLM 키를 설정할 필요는 없다.

### 실행 단계

| `--phase` | 실행 항목 |
| --- | --- |
| `smoke` | 마이그레이션, 프로세스 기동, 복제본별 요청 라우팅, 두 계정의 로그인·조직 격리, 종료 후 자원 회수 |
| `api` | smoke와 HTTP 검수. 실행·중복 요청, 연결 해제와 다른 워커에서 복구, 종료, 승인·거절, 기존 대화 및 주변 API 호환성을 검사한다. |
| `browser` | smoke와 Chromium 검수. 두 전송 방식에서 스트리밍 시간을 각각 측정한다. 전환 테스트는 같은 대화의 위임·승인·다른 워커에서 읽기·롤백과, 진행 중 전환에서 일시정지 목표·Steer·입력 큐 보존을 확인한다. |
| `full` | 같은 격리 스택에서 API 검수 후 브라우저 검수를 실행한다. 호환성 회귀 spec은 아래처럼 별도로 지정한다. |

아래 네 명령에서 필요한 단계만 실행한다. 출력 경로는 실행마다 새 디렉터리여야 한다. 제품 코드를 수정한 뒤에는 검수 대상 커밋을 새로 지정한다.

```bash
PY="$PWD/backend/.venv/bin/python"
RUN=scripts/ag_ui_acceptance/run.py
SHA=$(git rev-parse HEAD)
OUT="$PWD/output/e2e-captures/$(date +%Y%m%d-%H%M%S)-ag-ui"

"$PY" "$RUN" --revision "$SHA" --phase smoke   --output "$OUT/smoke"
"$PY" "$RUN" --revision "$SHA" --phase api     --output "$OUT/api"
"$PY" "$RUN" --revision "$SHA" --phase browser --output "$OUT/browser"
"$PY" "$RUN" --revision "$SHA" --phase full    --output "$OUT/full"
```

### 호환성 회귀를 포함한 검수

`--extra-spec` 한 개당 Playwright 선택자 하나를 전달한다. AG-UI 전용 검수에는 `--ag-ui-spec`을 사용한다. 실행기는 선택한 케이스를 **두 프런트엔드에서 각각** 실행한다. 아래 두 줄 번호는 문서 뷰어 4종과 대화 전환 시 사용량 초기화 테스트를 선택한다. 대상 커밋에서 줄이 바뀌면 두 테스트 선언 위치를 확인한다.

```bash
specs=(
  chat-interactions.spec.ts chat-attachments-display.spec.ts
  hitl-approval.spec.ts tool-approval.spec.ts
  chat-generative-ui.spec.ts chat-mcp-apps.spec.ts
  chat-side-chat.spec.ts skill-builder-chat.spec.ts
  chat-goal-steer-notice.spec.ts chat-message-queue.spec.ts
  chat-pinned-summary.spec.ts chat-run-summary.spec.ts
  document-artifact-viewers.spec.ts:398 chat-langgraph-v3-regressions.spec.ts:505
)
extra=()
for spec in "${specs[@]}"; do extra+=(--extra-spec "e2e/$spec"); done
"$PY" "$RUN" --revision "$SHA" --phase full --output "$OUT/parity" "${extra[@]}"
```

목표 알림 spec은 `E2E_CHAT_GOAL_NOTIFICATIONS=1`, MCP Apps spec은 `E2E_TEST_HELPERS_ENABLED=true`가 필요하며 실행기가 이를 설정한다. 독립 작업의 중복 제출·재접속도 검수하려면 테스트용 모델 서버의 `127.0.0.1` 주소만 허용하는 환경에서 `--extra-spec e2e/independent-chat-jobs.spec.ts`를 추가한다. 선택한 케이스가 skip되거나 재시도되면 실패로 처리한다.

동일 역할의 두 하위 작업 중 지정한 작업만 보충 지시를 받는지도 검사하려면 `--delegation-probe scripts/ag_ui_acceptance/delegation_parity.py`를 추가한다. 이 검사와 F05 브라우저 검수에는 일반 워커를 정리한 뒤 같은 포트에 새 워커를 띄운다. 로컬 모델 접속은 `127.0.0.1`로 제한하고 설치된 제품이나 다른 검수 스택의 설정을 바꾸지 않는다.

### 공식 어댑터와 승인 응답 비교

`compare_approval.py`는 Moldy에서 실제 수집한 요청과 이벤트 두 건을 별도 환경의 공식 어댑터에 대조한다. 준비된 환경이 있으면 그 Python을 `REF_PY`로 지정한다. 새로 준비하려면 아래 세 패키지를 고정한다. 이 설치는 검수용이며 제품 의존성 변경과 별개다.

```sh
REF_DIR=$(mktemp -d)
uv venv --python 3.12 "$REF_DIR/venv"
REF_PY="$REF_DIR/venv/bin/python"
uv pip install --python "$REF_PY" 'ag-ui-langgraph[fastapi]==0.0.48' \
  'ag-ui-protocol==1.0.0' 'langgraph==1.2.24' httpx
"$REF_PY" scripts/ag_ui_acceptance/compare_approval.py --help
"$REF_PY" scripts/ag_ui_acceptance/compare_approval.py \
  "$OUT/api/ag-ui-approval-single-interrupt.json" \
  "$OUT/api/ag-ui-approval-single-continuation.json" > "$OUT/api/reference-approval.json"
```

최종 검수에서는 위의 전체 회귀 명령에 `--reference-python "$REF_PY"`를 추가한다. 로컬 참조 그래프가 반환하는 승인 스키마, interrupt ID와 `resolved` 응답의 대응, 같은 스레드 내 새 run ID, 재개 후 성공 여부를 검사한다. 실행기가 저장한 비교 결과는 `--output` 아래의 `reference-comparison.json`에 있다. `stack.json`의 `reference_comparison.status`가 `not_requested`면 이 비교를 실행하지 않은 것이다.

### 결과 해석

- `--output` 아래의 `stack.json`에서 제품 SHA, lockfile 해시, 실행한 단계, 브라우저 케이스별 결과·관찰값, `passed`와 `cleaned`를 확인한다. `replicas.json`은 프록시를 경유한 요청이 도착한 API를 기록한다. API 단계의 `http-contract-results.json`에 실제 요청 결과를, `sdk-transcripts.json`에 공식 TypeScript 클라이언트로 검증한 시작·재접속·단일·혼합·거절 승인 기록과 누락 항목을 남긴다. 실패로 중단된 실행은 이미 끝난 단계만 담을 수 있다.
- 실제 HTTP·SSE, 체크포인트 저장과 조회, 권한 확인, 브라우저 렌더링, 승인 후 DOCX 하위 프로세스 실행은 로컬 제품 경로를 지난다. 모델 응답과 입력은 재현용 스크립트이며 유료 모델, 외부 공급자 장애, 실제 청구 비용은 이 실행의 측정 대상 밖이다.
- 측정용 `--perf-timing`을 지정하면 같은 6개 조각에 대해 테스트 모델 출력, PostgreSQL 커밋, Chromium 네트워크 도착, 화면 표시 시각을 기록한다. 각 프런트엔드에서 첫 텍스트 표시까지 **20초 미만**, 전체 텍스트 표시까지 **40초 미만**, 첫 표시와 마지막 표시 간격 **1.5초 초과**를 요구한다. `stack.json`의 `browser`에서 두 측정값을 직접 비교한다. 이 숫자는 로컬 스트리밍 기준이며 운영 환경의 p95를 추정하는 값으로 사용할 수 없다.
- 기본 전송 방식 변경은 별도 결정이다. 동일 SHA의 전체 회귀 결과, 공식 어댑터 비교, 전환·롤백 후 대화와 승인 상태, 중복 부수효과 및 다른 테넌트에 노출된 데이터가 각각 0건인지 확인한 후 결정한다.
