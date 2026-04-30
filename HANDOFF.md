# 작업 인계 문서

**브랜치**: `feature/greenfield-credentials`  **마지막 커밋**: `d1779c9`
**테스트**: 674 backend pytest PASS · frontend lint+build PASS

---

## 이번 세션 완료 (m23~m25 + UX 통합)

### 도메인/마이그레이션
- **m23** `models.default_credential_id` — 모델 추가 시 사용자 선택 영구화
- **m24** `credentials.is_system` — 운영자/사용자 키 분리
- **m25** `agent_mcp_tools` link table — m5 follow-up 완성 (agent ↔ MCP 도구)
- 카탈로그 lookup canonicalization (ai-model-list `aliases` map 보존)

### Single Source of Truth
- `services/credential_resolver.py` (user) + `system_credential_resolver.py` (operator)
- `agent_runtime/credential_resolution.py` (agent → api_key tiered)
- `model_service.serialize_model` 단일 (router `_model_to_dict` 제거)
- frontend `lib/utils/credential-resolution.ts` (TS 미러)

### UX
- 사이드바 **Connectors 그룹** (Anthropic 멘탈 모델)
- **MCP wizard 4→3단계 + probe-only** ([Add] 시점에만 INSERT)
- **Model 배지** Catalog/Manual 통합
- **System Credentials 페이지** `/settings/system-credentials` (Fix Agent + 이미지생성)
- **통합 4탭 dialog** (Catalog/My Tools/MCP/Skills) — visual + form + manual 모두 사용

---

## 남은 작업

1. **SkillsNode도 통합 dialog 호출** (defaultTab='skills') — Toolbox만 적용됨
2. **middleware dialog를 서브에이전트 패턴**으로 단순화
3. **Catalog 탭 inline 인스턴스 생성** (현재는 `/tools` link 안내)
4. **옛 add-tools-dialog/add-skills-dialog 파일 제거**
5. **9개 옛 agent 깨진 model_id 정리** (사용자 결정 필요)
6. **PR 생성 + 머지**

---

## 환경

- backend `localhost:8002` (uvicorn --reload), frontend `3000`
- DB: **5432 본 환경** (m25 schema). 5433 테스트는 격리 유지
- `.env`: `DATABASE_URL=...localhost:5432/moldy` + `ENCRYPTION_KEYS=002f21a2...`
- 시스템 키: `/settings/system-credentials`에 Anthropic + OpenRouter 등록됨

---

## 주의사항

- **ENCRYPTION_KEYS 손실 금지** — 변경 시 모든 credential 재등록
- **사용자 vs 시스템 credential**: assistant/builder/이미지생성은 `is_system=True`만, 사용자 키는 agent chat에만
- **base-ui Select**: SelectItem children 라벨 추출 불안정 → 새 Select마다 `SelectValue` function children 사용
- 9개 옛 agent의 model_id가 invalid FK → UI에 "no model bound" 표시 (graceful 처리됨)

---

## 핵심 파일

- Resolver: `backend/app/services/{credential_resolver,system_credential_resolver}.py`, `backend/app/agent_runtime/credential_resolution.py`, `frontend/src/lib/utils/credential-resolution.ts`
- 통합 dialog: `frontend/src/components/agent/visual-settings/dialogs/tools-skills-dialog.tsx`
- Catalog: `backend/app/services/model_catalog/{rules,resolve,merge}.py`
- System credentials: `frontend/src/app/settings/system-credentials/page.tsx`

---

새 세션에서 "HANDOFF.md 읽고 이어서" 하면 즉시 컨텍스트 복원 가능.
