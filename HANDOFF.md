# 작업 인계 문서

**브랜치**: `feature/greenfield-credentials`  **검증**: backend pytest + frontend lint/build PASS

---

## 이번 세션 완료 (UI 통합 + MCP/skill 백엔드 정합성)

### Frontend dialog 통합
- `ToolsSkillsDialog` 재설계 — **좌측 통합 (Tool/MCP/Skill 아이콘 구분)** + 우측 탭. `mode` prop:
  - `all` (form-mode/manual): 4탭
  - `tools` (visual ToolboxNode): 3탭, 제목 "도구 추가"
  - `skills` (visual SkillsNode): 탭 없이 SubAgents 패턴
- Catalog 탭 → 1열 row + `[+ 만들기]` (deep-link `/tools?create=<key>`, inline 생성은 보류)
- 통합 `MiddlewaresDialog` 신규 (SubAgents 2-column + 카테고리 chip). visual+form 양쪽 사용
- 공통 `LineTabsList`/`LineTabsTrigger` (variant=line + emerald) → 통합 dialog/right-panel/manual-page 적용
- 검색 input ring 제거 (`focus-visible:ring-0`)
- 옛 파일 제거: `add-tools-dialog.tsx`, `add-skills-dialog.tsx`, `add-middlewares-dialog.tsx`

### Frontend bugfix
- form-mode `ToolsSkillsBox` MCP 누락 → `useAllMcpTools` 추가, "도구·MCP·스킬" 카운트 + ServerIcon 행
- `section-sub-agents.tsx` UI를 `section-model.tsx` 패턴으로 통일 (py-3 / text-sm / Button icon-sm)
- 미들웨어 박스 라벨 "미들웨어" → `common.add: "추가"`
- visual-settings `SubagentsNode` 활성화 — `SubAgentsDialog` 호출 + edge animated 활성
- `/agents/[id]/settings/page.tsx` sync 누락 — **`selectedMcpToolIds` 동기화 추가** (first-sync + dirty-aware refetch)

### Backend
- `services/tool_service.get_tools_catalog` → MCP 도구도 join (Fix 에이전트 `list_available_tools`가 MCP까지 응답)
- `assistant/tools/write_tools.py` — **`add_mcp_tool_to_agent` / `remove_mcp_tool_from_agent` 신규**, prompt.md 업데이트
- `helpers.get_agent_with_eager_load`에 `mcp_tool_links` selectinload 추가
- `routers/agents._agent_to_response` SkillBrief에 **`slug`+`kind`** 누락 → 500 회귀 수정 (skill 연결 후 `/api/agents` 전체 죽음)
- 사용자 스킬 `mark_seat.py` 두 곳 하드코딩 `/mnt/user-data/outputs/` → `os.environ.get("OUTPUTS_DIR")` 사용

---

## ⚠️ 분석으로 발견된 큰 누락

### Sub-agents가 deepagents 정석 통합 안 됨
`executor.build_agent`가 `create_deep_agent(subagents=...)` 인자를 **전혀 전달하지 않음**. agent.sub_agent_links → SubAgent dict 변환 코드 0건. UI/Fix로 sub-agent 추가해도 task 도구는 빌트인 `general-purpose`만 호출 가능. 시스템 프롬프트의 *"only general-purpose 허용"*이 임시 우회 (executor.py:506).

**다른 4개는 정석 통합 OK**:
- Tools ✅ / MCP ✅ (langchain-mcp-adapters) / Middleware ✅ (langchain.agents.middleware) / Skills ✅ (FilesystemBackend + SkillsMiddleware) / Memory ✅

---

## 남은 작업

1. **Sub-agents 정석 통합** (큰 작업, 사용자 컨펌 필요)
   - `chat_service.AgentConfig`에 `subagent_specs: list[dict]` 추가
   - `chat_service`가 `agent.sub_agent_links → SubAgent dict` 변환 (name/description/system_prompt + 도구·모델·미들웨어 prefetch)
   - `executor.build_agent`에 `subagents` 매개변수 + `create_deep_agent(subagents=...)` 전달
   - executor.py:500-509의 임시 우회 프롬프트 제거
2. **Catalog 탭 inline 인스턴스 생성** — 보류 (페이지 이동 vs dialog 내 mini-form 결정 필요)
3. **9개 옛 agent 깨진 model_id 정리** — 사용자 결정 필요 (삭제/디폴트 할당/그대로)
4. **PR 생성 + 머지**

---

## 핵심 파일

- 통합 dialogs: `frontend/src/components/agent/visual-settings/dialogs/{tools-skills,middlewares}-dialog.tsx`
- 공통 탭: `frontend/src/components/ui/line-tabs.tsx`
- form-mode 동기화: `frontend/src/app/agents/[agentId]/settings/page.tsx` (selectedMcpToolIds 추가)
- Fix agent write: `backend/app/agent_runtime/assistant/tools/{write_tools,helpers}.py`
- Catalog: `backend/app/services/tool_service.py` (MCP join)
- 직렬화 fix: `backend/app/routers/agents.py` (_agent_to_response SkillBrief slug/kind)

---

## 환경

- backend `localhost:8002` (uvicorn --reload), frontend `3000`
- DB 5432 본 환경 (m25 schema). 5433 테스트는 격리.
- ENCRYPTION_KEYS 손실 금지

새 세션에서 "HANDOFF.md 읽고 이어서" 하면 즉시 컨텍스트 복원.
