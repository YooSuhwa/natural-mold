# 작업 인계 문서 — System LLM Settings + LiteLLM 연동

**세션**: 2026-05-26
**목표**: System 기능(Builder/Assistant/이미지)을 운영자가 UI에서 역할별 모델 선택 + LiteLLM(openai_compatible) 엔드포인트로 구동

## 완료된 작업
- [x] **ADR-019 System LLM Settings** — PR #168 **머지됨**. `system_llm_settings` 테이블(m45) + `resolve_system_model(role)` + base_url 주입 + `/settings/system-llm` 화면(슬롯 3개)
- [x] **LLM 키 문서 정정** — PR #169 **머지됨**. ENV 키 필수→선택(UI 등록), `.env.example`/CLAUDE/README
- [x] **LiteLLM/builder 통합 버그 3건** — PR #170 **리뷰 대기**(push 완료)
  - (a) discover-models가 system credential 404 → super_user면 `get_system` 폴백
  - (b) builder JSON 파싱 → `raw_decode`로 trailing 텍스트 무시(LiteLLM 호환)
  - (c) builder 채팅 중복 crash → `allMessages` id dedup(assistant-ui 불변식)

## 진행 중인 작업
- [ ] **PR #170 머지** — 리뷰/머지 대기. 머지 후 `/sync`

## 다음에 해야 할 작업
1. PR #170 머지 → main `git pull`(또는 /sync)
2. **운영자 셋업**(ADR-019 의도된 breaking): super_user가 `/settings/system-llm`에서 text_primary/text_fallback/image 3슬롯 선택해야 Builder/Assistant/이미지 동작. m45는 이미 적용됨.
3. follow-up(별도 PR): **OPEN-2** aiosqlite 전역 `PRAGMA foreign_keys=ON`(타 FK SET NULL 잠재 거짓통과) / **OPEN-4** 운영자 경고박스 raw `bg-amber-*`→`--status-warn` 토큰화(system-llm·system-credentials 동시)
4. (선택) builder `streamingMessages` 미클리어로 인한 미세 메모리 누적 — (c)는 dedup으로 crash만 방어. 근본(builder 흐름 클리어)은 W3-out 가드와 얽혀 보류

## 주의사항
- **DB 단일 source**(ADR-019 결정2): `.env` builder_model_*/assistant_model_* 런타임 미사용. 미설정 시 `SystemModelNotConfiguredError`로 명시 실패
- **건드리면 안 됨**: `backend/data`·`backend/.env`(worktree symlink, 커밋 제외), `use-chat-runtime.ts`의 streamingMessages 클리어 로직(W3-out 스트림 재개 가드)
- LiteLLM gateway 모델은 JSON 뒤 텍스트를 붙이는 경향 → (b)가 방어
- 머지된 worktree `.claude/worktrees/system-llm-settings`는 `git worktree remove` 가능(ADR-018로 데이터 손실 없음)

## 관련 파일
- `docs/design-docs/adr-019-system-llm-settings.md`
- 백엔드: `app/models/system_llm_setting.py`, `app/services/system_credential_resolver.py`, `app/routers/system_llm_settings.py`, `alembic/.../m45_*.py`
- 배선: `assistant_agent.py`, `builder/sub_agents/helpers.py`, `image_service.py`, `builder_v3/image_gen.py`
- 프론트: `frontend/src/app/settings/system-llm/`, `lib/chat/use-chat-runtime.ts`
- fix: `app/routers/credentials.py`(discover)

## 마지막 상태
- 브랜치: `fix/litellm-builder-integration` (main 체크아웃 디렉토리)
- 마지막 커밋: `b611ff4`
- 테스트: backend **1221 passed / 0 회귀**, frontend build·lint 그린
- PR: #168 머지 · #169 머지 · #170 리뷰 대기
