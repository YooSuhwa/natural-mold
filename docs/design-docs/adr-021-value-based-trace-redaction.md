# ADR-021: Value-Based Trace Redaction (값 기반 트레이스 시크릿 마스킹)

- **상태**: 제안됨 (2026-06-24)
- **DRI**: chester
- **관련**: ADR-007/009/013 (Credentials), ADR-011 (SSE Stream Resume), ADR-017 (Marketplace Resources — `redact_credential_values` 출처)
- **영역**: `app/agent_runtime/protocol_redaction.py`, `app/agent_runtime/run_secrets.py`(신규), `app/services/conversation_stream_service.py`, `app/agent_runtime/runtime_component_builder.py`, `app/agent_runtime/agent_stream_runner.py`, `app/marketplace/redaction.py`(재사용)

---

## § 맥락

`redact_protocol_data`(`protocol_redaction.py`)는 trace/persistence/SSE egress 5개 지점에서 시크릿을 가린다:

| 호출처 | 무엇을 | 노출 대상 |
|--------|--------|-----------|
| `protocol_persistence.py:23` | LangGraph 이벤트 data | DB 영속화(`message_events`) |
| `langgraph_streaming.py:189` | 동일 이벤트 | SSE 클라이언트 전송 |
| `conversation_agent_protocol_state*.py` | state snapshot(messages/values) | state API 응답 |
| `chat_service.py:491` | tool_calls | 메시지 저장 |
| `trace_debug_service.py:52` | langfuse observation 입출력 | 운영자 debug trace 화면 |

에이전트 실행 중 `{{$credentials.x}}` 보간(ADR-009)으로 도구/MCP에 주입된 API 키·헤더(Authorization/Cookie)·DSN이 tool 입출력·state에 남기 때문에, 저장/전송/노출 전에 마스킹한다.

**문제**: 현재 방식은 시크릿이 **무엇인지 모르는 채** 키 이름 휴리스틱(`SENSITIVE_PROTOCOL_KEY_RE`) + 값 모양 정규식(`_VALUE_MASK_PATTERNS`)으로 **추측**한다. 이 추측 접근은 3차례 연속 회귀했다:

1. substring 키 매칭 → over-redaction(`session_id`/`token_count`) + leak(키 없는 `Bearer`, `{name,value}` 헤더)
2. 단어 경계 전환 → ReDoS(와일드카드 겹침) + camelCase 키 누출
3. DSN 스킴 일반화 → 더 심한 ReDoS(96KB=23초) + 두문자(acronym) 키 누출

매 수정이 새 엣지를 만든다. 정규식 기반 추측의 근본적 한계다.

## § 핵심 통찰

**정답이 이미 코드베이스에 있다.** `app/marketplace/redaction.py:51`의 `redact_credential_values(text, mapped_env_vars)`는 **실제 주입된 시크릿 값을 정확히 substring 치환**한다 — min-length threshold(`_MIN_REDACT_LEN=5`), 길이 내림차순 정렬(부분 치환 방지) 포함. `skill_executor.py:202`가 이미 subprocess 출력에 사용 중인 검증된 함수다.

이 시스템은 credential 시스템을 갖고 있어 **실행 시 실제 주입되는 시크릿 값을 안다.** 추측할 필요가 없다. 유일한 공백은 "그 값 집합을 trace egress 5곳까지 전달"하는 것이다.

## § 결정

**값 기반 마스킹을 1차 방어로, 휴리스틱을 보조 fallback으로** 둔다.

1. run당 평문 시크릿 값을 수집해 run-scoped set으로 모은다.
2. ContextVar로 redaction call site까지 전파한다.
3. `redact_protocol_data`가 휴리스틱 **앞단**에서 그 값들을 정확 substring 치환한다(`redact_credential_values` 로직 재사용).
4. 휴리스틱은 "DB에 없는 시크릿"(LLM이 응답에 생성한 토큰 등)용으로 축소 유지하며, ReDoS 표면이 큰 패턴(URL/DSN userinfo)은 제거/축소한다.

값 기반 exact-substring 치환은 단일 패스 `str.replace`라 **ReDoS가 원천적으로 불가능**하다.

## § 상세 설계

### 1. 값 수집
run당 평문 시크릿이 모이는 지점:
- **eager**(요청 진입 시): `conversation_stream_service._build_cfg`(라인 108–160) — LLM `api_key`, `tools_config[*]["credentials"]`, `mcp_transport_headers`.
- **lazy**(executor 단): `runtime_component_builder._prepare_runtime_components`(라인 576) — skill `descriptor.credential_bindings[*].decrypted`. subagent도 같은 경로.

신규 헬퍼 `collect_run_secret_values(cfg, *, extra=None) -> set[str]`로 두 지점에서 set에 union. 필터: `isinstance(str)` + `len >= 임계값`, dict/list 재귀 평탄화. `Bearer `/`Basic ` 접두를 분리한 토큰 본체도 함께 적재(접두 없이 echo되는 경우 대비).

### 2. 전파 — ContextVar
`app/agent_runtime/run_secrets.py`(신규)에 `ContextVar[set[str] | None]`. `agent_stream_runner._run_agent_stream`(라인 113–183)에서 `set()`, `finally`에서 `reset()`(기존 langfuse flush finally 활용).

**async/streaming 경계 검증**: `emit` 클로저(`langgraph_streaming.py:176`)와 persistence 적재는 `_run_agent_stream`과 **동일 async 태스크**에서 직선 실행되므로 ContextVar가 yield를 넘어 유지된다. fire-and-forget DB write 태스크는 `copy_context()`로 set 이후 값을 복사하므로 이중 안전. subagent는 같은 run 태스크라 set 객체를 in-place union(frozenset 아님).

**기각한 대안**: `config["configurable"]` 주입(checkpointer DB로 시크릿 유출 위험 — 절대 금지), AgentConfig 명시 인자(cfg 없는 순수 함수들까지 호출 그래프 오염), thread_id 글로벌 레지스트리(메모리 누수/race).

### 3. Redaction 인터페이스
```python
def redact_protocol_data(method, data, *, redact_memory=True,
                         secret_values: Iterable[str] | None = None) -> Any
```
`secret_values=None`이면 진입부에서 ContextVar 조회 → 5개 call site **코드 변경 없이** 자동 동작(시그니처 하위호환). 값 마스킹(`_mask_known_values`)을 키 휴리스틱 앞에 적용. 길이 내림차순 + min-length 필터.

**한계(문서화)**: 인코딩/변환된 시크릿(base64/url-encode/JSON-escape/부분 노출)은 exact-match 안 됨 → 휴리스틱 fallback 유지로 보완.

### 4. Fallback 휴리스틱 축소
- **유지**: 키 기반 마스킹(`_is_sensitive_protocol_key` — 키 길이 무관, ReDoS 무관), 앵커드 값 패턴(`Bearer\s+\S+`, JWT `\beyJ...`, `sk-...`).
- **제거/축소**: URL/DSN userinfo 패턴(ReDoS 진원지) — 값 기반이 DB 유래 DSN을 직접 마스킹하므로 잉여. `SENSITIVE_ASSIGNMENT_RE`는 값 기반에 우선순위 양보.

### 5. trace_debug_service 경로
사후 렌더라 ContextVar가 없다. 그러나 **persistence-time 마스킹이 정확하면 저장된 `message_events`는 이미 깨끗**하다(`protocol_persistence.py:23`에서 이미 마스킹됨). Langfuse 경로는 capture 시점 `mask`(`langfuse.py:176`)에 위임 — 필요 시 capture 시점 ContextVar 통합(M3, 선택).

## § 점진 롤아웃 (no big-bang)

| 단계 | 내용 | done-when |
|------|------|-----------|
| M0 | `run_secrets.py`(ContextVar+헬퍼) + set/reset 배선. redaction 동작 불변(휴리스틱 100% 유지) | 기존 테스트 그린, 회귀 0 |
| M1 | `redact_protocol_data`에 값 마스킹 레이어 추가(휴리스틱 앞). ContextVar 비면 no-op | 값 기반/휴리스틱 둘 다 테스트 통과(중첩 방어) |
| M2 | 트레이스 샘플로 커버리지 측정 후 URL/DSN userinfo 휴리스틱 축소/제거 | 회귀 가드 통과 |
| M3(선택) | Langfuse capture 시점 ContextVar 통합 | — |

각 단계 독립 PR, 휴리스틱은 M2까지 살아있어 값 기반 공백을 보호.

## § 영향
- **DB 마이그레이션 없음** (런타임 동작 변경만).
- 시그니처 하위호환(keyword-only optional) → 5개 call site 무수정.
- M1 이후 새 `message_events`가 더 깨끗. 기존 저장 데이터는 불변(백필은 Open Question).
- ContextVar 미설정(단위 테스트/트리거 모드)에선 값 마스킹 skip + 휴리스틱만 → 기존 테스트 그린.

## § Open Questions (결정 필요)

1. **min-length/엔트로피 임계값**: 짧은 패스워드(`hunter2` 7자)까지 잡으려면 임계를 낮춰야 하나 일반 텍스트 과잉 마스킹 위험↑. 5자(현 marketplace) vs 8~12자+엔트로피(`secret_scan._is_opaque_secret_run` 재사용).
2. **URL/DSN userinfo 휴리스틱**: 완전 제거 vs 더 엄격한 bounded 유지("LLM이 생성한 임의 DSN"은 값 set에 없어 못 잡음).
3. **인코딩 변형 시크릿**: url-quote/base64/JSON-escape 변형본을 수집 set에 함께 적재할지(커버리지↑/메모리·성능↓). 1차는 raw만 권장.
4. **Langfuse capture 통합(M3)**: 범위 포함 vs Langfuse 자체 `mask` 위임.
5. **과거 저장 데이터 백필**: 기존 `message_events` 잠재 누출 — 이번 범위 포함 vs 별도 태스크.
