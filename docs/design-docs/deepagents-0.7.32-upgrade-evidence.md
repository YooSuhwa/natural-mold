# Deep Agents 0.7.11 → 0.7.32 상향 근거 (기획서 v2 14.4절, 사후 작성)

> 대상: #330(1bc53bb1). 기획서 v2 14.4절 1단계의 "기준 결과 비교"와 "기본·lease 경로 각각 시험", "심볼릭 링크 권한 시험"을 사후에 채운다([feature-plan-v2-gap-audit.md](feature-plan-v2-gap-audit.md) 3-1·3-2·3-3).
> 작성: 2026-11-05. 다음 patch부터는 올리기 전에 이 표를 먼저 만든다.

## 1. 함께 올린 패키지

| 패키지 | 이전 | 이후 |
|---|---|---|
| deepagents | 0.7.11 | 0.7.32 |
| langchain | 1.3.18 | 1.4.7 |
| langchain-core | 1.6.1 | 1.7.3 |
| langgraph | 1.2.11 | 1.2.19 |
| langchain-anthropic | 1.7.0 | 1.7.7 |
| langchain-google-genai (google-genai) | 4.3.7 (1.70.0) | 4.4.2 (2.34.0) |

## 2. 기준 결과 비교

판정은 기획서의 네 분류(표현 변경, 권한, 재개, 자료 변경)에 "동작 변경"을 더했다. 근거는 #330의 코드·계약 픽스처 변경과 테스트다.

| 비교 항목 | 차이 | 분류 | 근거 |
|---|---|---|---|
| 실제 도구 목록 | 없음 | — | `tests/agent_runtime/fixtures/runtime_facade_v1.json` 변경 없음 |
| 기본 미들웨어 종류와 순서 | 그래프에 `_ParentNotifierMiddleware.after_agent` 노드 추가. 비동기 하위 에이전트에서만 동작하고 Moldy는 쓰지 않음 | 표현 변경 | `runtime_profiles_v1.json` 한 줄 추가 |
| 파일 권한 | 규칙은 같음. 0.7.30부터 에이전트와 파일시스템 미들웨어의 규칙이 같아야 해서, Moldy 파일 계층의 규칙(오프로드 읽기 포함)을 에이전트·선언형 하위 에이전트·범용 하위 에이전트에 똑같이 넘기도록 바꿈 | 권한 (규칙 동일, 전달 경로만 바뀜) | `deep_agent_factory._agent_permissions`, `tests/test_filesystem_permissions.py` |
| 심볼릭 링크 | Moldy 실행 파일 계층은 링크를 따라가지 않는다. 같은 범위 안을 가리키는 링크도 열지 않는다 | — (상향 전후 같음) | 새 시험 `test_symlinks_in_an_allowed_scope_do_not_reach_denied_paths`. 링크를 따라가는 기본 `FilesystemBackend`로 바꾸면 실패함을 확인 |
| 체크포인트 읽기와 재개 | 승인 대기 이벤트(`Interrupt`)에 `response_schema`가 생겨, 직렬화 순서를 바꿔 기존 형식 `{value, id}`을 유지 | 재개 (형식 유지) | `langgraph_protocol_adapter.py`, 재개 계약 시험 통과 |
| 저장된 정책 해시와 프리셋 의미 | 없음 | — | `runtime_policy` 코드 변경 없음 |
| 제공사 호출과 비용 | 입력 예산이 시스템 프롬프트와 도구 스키마까지 세고, 요약 뒤에도 넘치면 요청을 보내지 않고 거부한다 | 동작 변경 | 요약(compaction) 시험의 문맥 창을 그 오버헤드만큼 키움 |
| 비밀값 가리기 | 없음 | — | 관련 코드 변경 없음, 시험 통과 |

**확인하지 않은 것:** 0.7.11에서 만든 체크포인트를 0.7.32에서 읽고 재개하는 시험(기획서 14.4절 완료 증거 셋째 항목). 재개 계약 시험은 새로 만든 체크포인트만 쓴다.

## 3. 기본 경로와 prepared lease 경로

백엔드 전체 시험을 `PREPARED_RUN_LEASE_ENABLED=true`로 다시 돌렸다(기본값은 false).

| 실행 | 결과 |
|---|---|
| 0.7.32 (main), lease 켬 | 9,565 통과, 19 실패 |
| 0.7.11 (#330 직전 커밋), lease 켬 | 같은 19개를 포함해 54 실패 |
| 0.7.32, lease 끔 (기본) | 전부 통과 |

- **0.7.32에서만 실패한 시험은 0개다.** 19개는 모두 상향 전에도 실패했다.
- 19개: `test_trigger_executor.py` 12, `test_f08_trigger_blocked.py` 2, `test_agent_lifecycle_trigger_runtime.py` 2, `test_chat_integration.py` 1, `test_prepared_agent_api_contract.py` 1, `test_prepared_run_adapter.py` 1("lease 기본값은 꺼짐"을 확인하는 시험이라 켜면 당연히 실패). 대부분 기본 경로를 모킹하는 시험이라, lease를 켜면 실행이 lease 경로로 가서 모킹되지 않은 자격 증명 확인(`llm_credential_required`)에 걸린다. 상향과 무관한 시험 구성 문제다.
- 0.7.11 쪽의 나머지 35개는 저장소 밖 임시 작업 폴더에서 돌린 탓에 실패한 스크립트·git 시험이다.
- 따라서 lease 경로 회귀는 이 시험 묶음으로는 판정할 수 없다. lease 경로 전용 시험(`tests/authz/test_prepared_*`, `test_lease_*`)은 lease 꺼짐에서도 돌고 통과한다. 19개를 lease 켬에서도 통과하게 만드는 일은 lease를 기본값으로 바꾸기 전에 한다.
