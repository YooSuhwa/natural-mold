# HANDOFF — deepagents 0.6 업그레이드

**Branch**: `chore/deepagents-0.6`
**Date**: 2026-05-18
**Base**: `ce5b8d8` (main 머지 직후)
**Status**: ✅ 코드 안정 — 라이브 검증 완료 / main 머지 대기

---

## 마지막 상태

- 커밋 10개 (`ce5b8d8..HEAD`)
- 검증: backend pytest **950 PASS**, ruff clean, frontend tsc(prod) clean, build PASS
- backend uvicorn (8001) / frontend pnpm dev (3000) 가동 중
- 미커밋: `model_catalog/*.json` 5개 (스케줄러 자동 갱신, 무관)

---

## 1. 작업 완료 — deepagents 0.5.6 → 0.6.1

### 패키지
- `deepagents` 0.5.6 → 0.6.1
- `langgraph` 1.1.10 → 1.2.0 (DeltaChannel 도입)
- `langchain` 1.2.17 → 1.3.0 (middleware 시그니처 변경)

### 해결한 회귀 (8건)

1. **DeltaChannel 메시지 추출** — `channel_values["messages"]`가 비어 `messages: []` 반환되던 회귀. `materialize_messages_at_checkpoint`로 `aget_delta_channel_history` 재생.
2. **alist 루프 + nested DB 호출 deadlock** — 2-phase 분리 (drain alist → materialize).
3. **fork-edit replace 동작 안 함** — ancestor `pending_writes` 누적. `Overwrite(value=[pre_msgs, new])` 로 채널 강제 리셋.
4. **regenerate replace 동작 안 함** — 같은 원인, 같은 패턴 적용.
5. **BranchPicker 위치/카운트 오류** — synthetic id(HumanMessage `id=None`) vs real id 분리 dedup, 자기 위치 쌍 키 비교.
6. **middleware 시그니처 (langchain 1.3)** — `ModelCallLimitMiddleware`/`PIIMiddleware` 필수 인자. `config_schema` default 자동 적용.
7. **WittyLoadingMessage 메시지 빠른 교체** — 모듈 글로벌 캐시(`_currentMessage`/`_nextRotateAt`) + `setTimeout` 체이닝으로 remount 무관 stable rotation.
8. **스트리밍 60 emits/sec 누적 비용** — `content_delta` 핸들러를 `requestAnimationFrame` 배치.

### 리팩토링
- `build_fork_overwrite_input` 헬퍼로 edit/regenerate Overwrite payload 통합.

---

## 2. 핵심 변경 파일

| 영역 | 파일 |
|------|------|
| Python deps | `backend/pyproject.toml`, `backend/uv.lock` |
| 미들웨어 호환 | `backend/app/agent_runtime/middleware_registry.py` |
| Executor dict 입력 | `backend/app/agent_runtime/executor.py` |
| Edit/Regenerate fork | `backend/app/routers/conversations.py` |
| DeltaChannel + 브랜치 트리 | `backend/app/services/thread_branch_service.py` |
| 테스트 갱신 | `backend/tests/test_thread_branch.py` |
| 스트리밍 rAF | `frontend/src/lib/chat/use-chat-runtime.ts` |
| 위트 로딩 | `frontend/src/components/chat/witty-loading.tsx` |

---

## 3. 다음 단계 (옵션)

- [ ] **main 머지** — PR 생성 또는 직접 머지
- [ ] **Phase 2 materialize 병렬화** (`_collect_checkpoints`) — `asyncio.gather` + 세마포어. 50+ checkpoint 스레드에서 list_messages 지연 감소. langgraph postgres pool 한도 확인 필요.
- [ ] **stream_events v3 마이그레이션** — 별도 PR. `agent.astream(stream_mode="messages")` → `agent.stream_events(version="v3")`. streaming.py 분기 단순화 가능.
- [ ] **WittyLoadingMessage assistant-ui 마운트 안정화** — 모듈 글로벌 캐시는 우회책. 정공법은 assistant-ui 재구성 원인 추적.

---

## 4. 알려진 한계

- **첫 user 메시지 fork-edit**는 root `__start__` write 때문에 어색했으나 `Overwrite` 패턴으로 해결됨.
- 사전 회귀 (이 PR 무관) — frontend test 5건 i18n 메시지 미스매치 (`api/client`, `providers/query-provider`, `sse/parse-sse`, `sse/stream-chat`). 별도 fix 필요.

---

## 5. 새 세션 시작 시

```
git checkout chore/deepagents-0.6
git log --oneline ce5b8d8..HEAD   # 10개 커밋 확인
uv run pytest tests/ -q            # 950 PASS 재확인 (backend 폴더에서)
```

브랜치 그대로 main 머지 / PR 생성하면 됨. 미해결 follow-up은 §3 참조.
