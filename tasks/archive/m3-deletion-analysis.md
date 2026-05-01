# M3 삭제 분석 보고서

## 요약
- **제거 대상**: 2개 파일(전체 삭제), 3개 함수(제거/단순화), 2개 config 설정, 2개 ruff 예외
- **테스트 영향**: 1개 파일 (`test_skill_package.py`) — 2개 클래스 전체 삭제, 1개 클래스 수정
- **총 제거 예상 라인**: ~217줄 (app/) + ~180줄 (tests/)

---

## 상세 분석

### 1. skill_tool_factory.py (전체 삭제 — 124줄)

| 함수 | 외부 참조 (app/) | 테스트 참조 | 안전 삭제 | 비고 |
|------|------------------|-------------|-----------|------|
| `_load_skill_metadata()` | 0 (내부 전용) | 0 | ✅ | create_skill_tools에서만 호출 |
| `create_skill_tools()` | executor.py:190 (lazy import) | test_skill_package.py 6건 | ⚠️ 선행 조건 있음 | executor.py skill_package 분기 제거 후 삭제 가능 |

**의존**: `skill_executor.py`를 import (L7)

### 2. skill_executor.py (전체 삭제 — 93줄)

| 함수/클래스 | 외부 참조 (app/) | 테스트 참조 | 안전 삭제 | 비고 |
|-------------|------------------|-------------|-----------|------|
| `SkillScriptResult` | 0 | 0 (간접 사용) | ✅ | skill_tool_factory에서만 사용 |
| `execute_skill_script()` | skill_tool_factory.py:7 (import) | test_skill_package.py 7건 | ⚠️ 선행 조건 있음 | skill_tool_factory.py 삭제 후 삭제 가능 |

### 3. chat_service.py — 함수별 분석

| 함수 | 라인 | 외부 참조 (app/) | 테스트 참조 | 액션 | 비고 |
|------|------|------------------|-------------|------|------|
| `get_agent_skill_contents()` | L145-163 | build_effective_prompt (같은 파일, L168) | test_skill_package.py 3건 | **삭제** | build_effective_prompt 단순화 후 참조 0 |
| `build_effective_prompt()` | L166-172 | conversations.py:115, trigger_executor.py:43 | test_skill_package.py 2건 | **단순화** | `return agent.system_prompt`로 변경 (스킬 주입 제거) |
| `build_tools_config()` | L175-213 | conversations.py:116, trigger_executor.py:44 | test_skill_package.py 2건 | **부분 제거** | L197-213 (skill_package 블록) 제거, tool_links 반복만 유지 |

**단순화 후 build_effective_prompt()**: 1줄짜리 함수. 인라인 가능하나 S2 설계 판단에 위임.

**build_tools_config() 후 잔여**: tool_links 반복만 남음 (L179-195). 기능 정상.

#### chat_service.py 임포트 정리

| 임포트 | 라인 | 현재 사용처 | 삭제 후 사용처 | 액션 |
|--------|------|-------------|----------------|------|
| `from pathlib import Path` | L7 | build_tools_config L202 (skill_package 블록) | 없음 | **삭제** |
| `from app.config import settings` | L12 | build_tools_config L202 (skill_package 블록) | 없음 | **삭제** |
| `from app.models.skill import AgentSkillLink` | L15 | get_agent_with_tools L139 (selectinload) | get_agent_with_tools L139 | **유지** (skill_links 로딩은 S3에서 필요) |

### 4. executor.py — skill_package 분기 (L189-199)

| 코드 블록 | 외부 참조 | 안전 삭제 | 비고 |
|-----------|-----------|-----------|------|
| `elif tool_type == "skill_package":` (L189-199) | tools_config에서 skill_package 타입 유입 | ✅ chat_service.py 수정과 동시 | chat_service.build_tools_config에서 skill_package 생성 제거 후 도달 불가 |

```python
# 제거 대상 (executor.py L189-199)
elif tool_type == "skill_package":
    from app.agent_runtime.skill_tool_factory import create_skill_tools
    langchain_tools.extend(
        create_skill_tools(
            skill_id=tc["skill_id"],
            skill_dir=tc["skill_dir"],
            conversation_id=tc.get("conversation_id"),
            output_dir=tc.get("output_dir"),
        )
    )
```

### 5. config.py — 설정 제거

| 설정 | 라인 | 사용처 | 삭제 후 사용처 | 액션 |
|------|------|--------|----------------|------|
| `skill_script_timeout` | L45 | skill_executor.py:27 | 없음 | **삭제** |
| `skill_max_output_bytes` | L46 | skill_executor.py:28, skill_tool_factory.py:82 | 없음 | **삭제** |
| `skill_max_package_bytes` | L47 | skill_service.py (업로드 검증) | skill_service.py | **유지** (업로드 시스템 존속) |
| `skill_storage_dir` | L44 | skill_service.py (저장 경로) | skill_service.py | **유지** |
| `conversation_output_dir` | L50 | chat_service.py:202, conversations.py:155 | conversations.py:155 (파일 서빙) | **유지** |

### 6. pyproject.toml — ruff per-file-ignores

| 항목 | 라인 | 액션 |
|------|------|------|
| `"app/agent_runtime/skill_executor.py" = ["ASYNC240"]` | L97 | **삭제** |
| `"app/agent_runtime/skill_tool_factory.py" = ["ASYNC240"]` | L98 | **삭제** |

---

## 테스트 영향 분석

### test_skill_package.py (유일한 영향 파일)

| 테스트 클래스 | 라인 범위 | 테스트 수 | 액션 | 사유 |
|---------------|-----------|-----------|------|------|
| `TestUploadSkillPackage` | ~L118-275 | ~10 | **유지** | skill_service.upload_skill_package 테스트, M3 무관 |
| `TestSkillRouter` | ~L240-275 | ~3 | **유지** | 라우터 엔드포인트 테스트, M3 무관 |
| `TestPromptInjection` | L282-369 | 7 | **수정** | 아래 상세 참조 |
| `TestSkillExecutor` | L377-459 | 7 | **전체 삭제** | execute_skill_script 삭제됨 |
| `TestSkillToolFactory` | L467-549 | 6 | **전체 삭제** | create_skill_tools 삭제됨 |

#### TestPromptInjection 수정 상세

| 테스트 메서드 | 액션 | 사유 |
|---------------|------|------|
| `test_text_skill_content` | **삭제** | get_agent_skill_contents 제거 |
| `test_package_skill_variable_substitution` | **삭제** | get_agent_skill_contents 제거 |
| `test_claude_skill_dir_substitution` | **삭제** | get_agent_skill_contents 제거 |
| `test_build_effective_prompt_no_skills` | **수정** | 단순화된 동작 검증 (항상 system_prompt 반환) |
| `test_build_effective_prompt_with_skills` | **삭제 또는 수정** | 스킬 주입 없어짐. 단순히 system_prompt 반환 검증으로 변경 가능 |
| `test_build_tools_config_with_package_skill` | **삭제** | skill_package 타입 더 이상 생성 안 됨 |
| `test_build_tools_config_text_skill_no_tool` | **삭제** | skill 관련 분기 자체가 없어짐 |

**임포트 수정 필요** (L18-22):
```python
# 변경 전
from app.services.chat_service import (
    build_effective_prompt,
    build_tools_config,
    get_agent_skill_contents,
)
# 변경 후
from app.services.chat_service import (
    build_effective_prompt,
    build_tools_config,
)
```

---

## 삭제 순서 (의존 그래프 기반)

```
Step 1: executor.py — skill_package elif 분기 제거 (L189-199)
        ↓ (skill_tool_factory의 유일한 app/ import 제거)
Step 2: chat_service.py — 동시 수정
        a) get_agent_skill_contents() 삭제
        b) build_effective_prompt() 단순화 (return agent.system_prompt)
        c) build_tools_config() skill_package 블록 제거 (L197-213)
        d) 임포트 정리 (Path, settings 삭제)
        ↓ (skill_package 타입 생성 경로 완전 제거)
Step 3: skill_tool_factory.py 삭제
        ↓ (skill_executor의 유일한 외부 import 제거)
Step 4: skill_executor.py 삭제
        ↓
Step 5: 부수 정리 (병렬 가능)
        a) config.py: skill_script_timeout, skill_max_output_bytes 제거
        b) pyproject.toml: ruff per-file-ignores 2줄 제거
        c) test_skill_package.py: TestSkillExecutor/TestSkillToolFactory 삭제, TestPromptInjection 수정
```

**순서 제약**:
- Step 1 → Step 3: skill_tool_factory의 app/ 참조가 executor.py에만 있으므로, Step 1 완료 후 삭제 안전
- Step 3 → Step 4: skill_executor의 app/ 참조가 skill_tool_factory에만 있으므로, Step 3 완료 후 삭제 안전
- Step 1 + Step 2: 동시 수행 가능 (독립적)
- Step 5: Step 3, 4 완료 후 수행

---

## 유지 항목 (삭제하면 안 되는 것)

| 항목 | 사유 |
|------|------|
| `AgentSkillLink` 모델 + `skill_links` relationship | S3에서 skills 경로 매핑에 필요 |
| `Skill` 모델 | 스킬 CRUD/업로드 시스템 존속 |
| `skill_service.py` | 스킬 업로드/관리 존속 |
| `skills.py` 라우터 | 스킬 API 존속 |
| `get_agent_with_tools()` 내 `selectinload(Agent.skill_links)` | S3에서 skills 경로 조회에 필요 |
| `config.skill_storage_dir` | 스킬 저장 경로 |
| `config.skill_max_package_bytes` | 업로드 제한 |
| `config.conversation_output_dir` | conversations.py 파일 서빙에 사용 |

---

## 체크리스트 (통합 검증용 — S5 베조스)

- [ ] `skill_tool_factory.py` 파일 존재하지 않음
- [ ] `skill_executor.py` 파일 존재하지 않음
- [ ] `grep -r "skill_tool_factory" backend/app/` → 0건
- [ ] `grep -r "skill_executor" backend/app/` → 0건
- [ ] `grep -r "create_skill_tools" backend/app/` → 0건
- [ ] `grep -r "execute_skill_script" backend/app/` → 0건
- [ ] `grep -r "SkillScriptResult" backend/app/` → 0건
- [ ] `grep -r "get_agent_skill_contents" backend/app/` → 0건
- [ ] `grep -r "skill_package" backend/app/` → 0건
- [ ] `grep -r "skill_script_timeout" backend/app/` → 0건 (config.py 포함)
- [ ] `grep -r "skill_max_output_bytes" backend/app/` → 0건 (config.py 포함)
- [ ] `build_effective_prompt()`가 `agent.system_prompt` 반환 (스킬 주입 없음)
- [ ] `build_tools_config()`에 skill_package 분기 없음
- [ ] pyproject.toml에 삭제된 파일의 ruff 예외 없음
- [ ] `uv run ruff check .` 통과
- [ ] `uv run pytest` 통과 (regression 없음)
- [ ] `AgentSkillLink`, `skill_links` 참조 정상 (삭제 안 됨)
- [ ] `skill_service.py`, `skills.py` 라우터 정상 작동
