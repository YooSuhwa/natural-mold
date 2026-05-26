# HANDOFF — ADR-019 System LLM Settings

**세션**: 2026-05-26 (TTH 멀티에이전트 사일로)
**브랜치**: `feature/system-llm-settings` (worktree, 미머지)
**worktree 경로**: `.claude/worktrees/system-llm-settings/`
**소스**: `docs/design-docs/adr-019-system-llm-settings.md`
**거버넌스**: `tasks/system-llm/` (CHECKPOINT/AUDIT/progress/deletion-analysis)

## 목적

System 기능(Builder / Assistant / 이미지 생성)이 호출하는 LLM 모델을 운영자가 UI에서 역할별로 선택하고, LiteLLM 등 OpenAI-compatible 엔드포인트(base_url)로 태울 수 있게 함. 기존엔 `.env` 하드코딩 + base_url 주입 경로 부재로 불가능했다.

## 변경 사항 요약

### Backend
- **신규 테이블 `system_llm_settings`** (Alembic `m45`, down_revision=m44): `role`(text_primary/text_fallback/image, UNIQUE + CHECK) × `credential_id`(FK credentials ON DELETE SET NULL, nullable) × `model_name`(nullable) × `updated_at`. 부팅 시 3 role row를 NULL로 시드. provider는 저장 안 하고 credential.definition_key에서 파생.
- **resolver** (`system_credential_resolver.py`): `resolve_system_model(db, role) -> ResolvedSystemModel(provider, model_name, api_key, base_url)` + `SystemModelNotConfiguredError`. base_url을 credential payload에서 추출. `resolve_system_api_key`는 ADR-013 호환 유지.
- **배선** (ADR-019 결정5): assistant_agent.py→text_primary(+base_url 전달, 미설정 시 SSE `event:error code=system_model_not_configured`), builder helpers.py→text_primary/text_fallback(`@functools.cache` 제거 + ResolvedSystemModel frozen 동등성 기반 경량 캐시로 성능회귀 완화), image_service.py/image_gen.py→image role(`resolve_image_base_url()` 단일 헬퍼).
- **API** (`routers/system_llm_settings.py`): GET(3 role)/PUT(/{role}) 모두 `require_super_user`. credential_id 부적합 시 404/422 **detail byte-identical**(enumeration oracle 방지).

### Frontend
- `lib/types|api|hooks` + `app/settings/system-llm/page.tsx` — super_user 가드, 슬롯 3개 카드(credential select → discover-models 재사용 → model select → PUT). configured 뱃지, base_url 표시, 엣지 상태 인라인 안내(미선택/discover 실패/빈 목록/불완전저장 가드). 사이드바 메뉴 + ko.json i18n.

## 아키텍처 결정 (ADR-019)

- **결정 2**: `.env` fallback 제거 — DB 단일 source. 미설정 시 조용히 넘어가지 않고 `SystemModelNotConfiguredError`로 명확히 실패.
- **결정 4**: credential 등록은 기존 System Credentials 화면, 신규 화면은 선택만. 전부 super_user 전용.
- **결정 5**: builder/assistant/image 호출부를 resolver로 교체, base_url 전달.

## 삭제/단절 (Musk Step 2 — 베조스 S1)
- `.env` 모델설정 런타임 read 18곳/4파일 단절 (config 상수 정의는 시드 참조용 잔존).
- `@functools.cache`(builder helpers) 제거 — 설정 변경 미반영 버그였음.

## Ralph Loop 통계
- 총 스토리 5 (S1~S5). 1회 통과 5 / 재시도 0 / 에스컬레이션 0.
- 품질 게이트: backend ruff clean + **pytest 1219 passed / 0 회귀**, frontend build + lint 그린.
- 베조스 사전리뷰가 "테스트 추가해도 거짓통과하는" 함정 2개(super_user 가드 무검증, FK PRAGMA 누락) 사전 차단 → 둘 다 머지 전 폐쇄.

## 남은 작업 (follow-up)
- [ ] **OPEN-2**: aiosqlite conftest 전역 `PRAGMA foreign_keys=ON` 채택 검토. 현재 FK SET NULL 테스트는 국소 engine+PRAGMA로만 가드(`test_credential_delete_sets_slot_null`). 전역 누락은 **builder_session 등 다른 FK SET NULL 테스트의 잠재 거짓통과** 가능성 — 별도 PR에서 전체 회귀 확인 후 적용.
- [ ] **OPEN-3 (배포 노트, 중요)**: 머지 후 운영자가 System LLM 설정 3슬롯을 선택하기 전까지 **Builder/Assistant/이미지 생성이 동작하지 않음** (ADR-019 결정2의 의도된 breaking). 배포 절차:
  1. `alembic upgrade head` (m45 — 3 role 시드 NULL)
  2. super_user가 기존 System Credentials 화면에서 LLM credential 등록
  3. 신규 `/settings/system-llm`에서 text_primary / text_fallback / image 3슬롯 선택
  - 이 셋업 전엔 system 기능 비가용. 미설정 시 조용한 실패 없이 `SystemModelNotConfiguredError`(assistant는 SSE `code=system_model_not_configured`)로 "운영자 설정 필요" 안내.
- [ ] PR 생성 + 머지. 머지 후 main `alembic upgrade head`(m45) 적용.

## 주의사항
- worktree 작업: `bash scripts/worktree-setup.sh` 1회(.env + backend/data symlink). `backend/data`는 symlink라 커밋 대상 아님(`git add` 시 제외 확인).
- 신규 `storage_path` 무관(ADR-018), data/ 건드리지 않음.
- base-ui Select: `onValueChange`가 `(value: string|null)` — 핸들러 파라미터 타입 주의(progress.txt 기록).

## 핵심 파일
- ADR: `docs/design-docs/adr-019-system-llm-settings.md`
- Model: `backend/app/models/system_llm_setting.py`, Alembic `m45_system_llm_settings.py`
- Resolver: `backend/app/services/system_credential_resolver.py`
- API: `backend/app/routers/system_llm_settings.py`, `schemas/system_llm_setting.py`
- 배선: `assistant_agent.py`, `builder/sub_agents/helpers.py`, `image_service.py`, `builder_v3/image_gen.py`
- FE: `frontend/src/app/settings/system-llm/`, `lib/{api,hooks,types}/system-llm-setting*`
- 테스트: `backend/tests/test_system_llm_settings.py` (20 PASS)
