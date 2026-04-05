# ADR-003: 스킬 + 메모리 전환 설계

## 상태: 승인됨

## 맥락

현재 Moldy의 스킬 시스템은 두 가지 커스텀 경로로 동작한다:

1. **Text 스킬**: `build_effective_prompt()`가 DB `content` 필드를 시스템 프롬프트에 직접 주입
2. **Package 스킬**: `skill_tool_factory.py`가 `run_*`, `read_*_file` LangChain 도구로 변환, `skill_executor.py`가 Python 스크립트 실행

이 방식의 문제점:
- **프로그레시브 디스클로저 없음**: 모든 스킬 콘텐츠가 시스템 프롬프트에 일괄 주입되어 토큰 낭비
- **이중 경로**: text/package 타입에 따라 완전히 다른 코드 경로, 유지보수 부담
- **커스텀 도구 오버헤드**: `skill_tool_factory.py` + `skill_executor.py`가 LangChain 도구를 수동 생성
- **메모리 부재**: 에이전트별 장기 기억(AGENTS.md) 시스템 없음

M1에서 `create_deep_agent`로 전환 완료되었으므로, deepagents 네이티브 `skills`/`memory` 파라미터를 활용하여 스킬과 메모리 시스템을 통합한다.

---

## 결정

### 1. Backend 선택: FilesystemBackend

```python
from deepagents.backends import FilesystemBackend

backend = FilesystemBackend(
    root_dir="./data",        # backend/data/ 디렉토리
    virtual_mode=True,        # 보안: 경로 탈출 방지
)
```

**선택 근거:**
- 스킬(SKILL.md)과 메모리(AGENTS.md) 모두 디스크에 존재 → FilesystemBackend가 자연스러움
- CompositeBackend의 StateBackend 기본 라우트는 불필요 (에이전트 스크래치패드 미사용)
- 단일 백엔드로 `/skills/`와 `/agents/` 경로를 모두 커버
- `virtual_mode=True`로 경로 탈출(`..`, `~`) 방지

**가상 경로 매핑:**

| 가상 경로 | 디스크 경로 | 용도 |
|-----------|------------|------|
| `/skills/{skill_id}/SKILL.md` | `data/skills/{skill_id}/SKILL.md` | 스킬 로딩 |
| `/skills/{skill_id}/references/` | `data/skills/{skill_id}/references/` | 참조 문서 |
| `/agents/{agent_id}/AGENTS.md` | `data/agents/{agent_id}/AGENTS.md` | 에이전트 메모리 |

### 2. Skills 경로 매핑

#### `_list_skills` 동작 분석

deepagents `SkillsMiddleware`의 `_list_skills(backend, source_path)` 함수는:

1. `backend.ls_info(source_path)` → 소스 디렉토리 내 항목 나열
2. `is_dir=True`인 항목(서브디렉토리)만 필터링
3. 각 서브디렉토리에서 `SKILL.md` 다운로드
4. YAML frontmatter 파싱 → `SkillMetadata` 반환

**기대 구조:**
```
source_path/           ← _list_skills에 전달되는 경로
└── skill-name/        ← 서브디렉토리 (is_dir=True)
    ├── SKILL.md       ← 필수 (frontmatter: name, description)
    └── ...            ← 참조 문서, 스크립트 등
```

**현재 디스크 구조:**
```
data/skills/                          ← source_path = "/skills/"
└── d9f14fdf-...-81ae53783ef4/        ← skill_id 서브디렉토리
    ├── SKILL.md                      ← ✅ 존재
    ├── scripts/
    ├── references/
    └── floor_images/
```

→ `skills=["/skills/"]`로 전달하면 `_list_skills`가 모든 스킬을 자동 탐색.

#### Per-Agent 스킬 필터링

현재 `_list_skills`는 소스 디렉토리의 **모든** 스킬을 반환한다. 에이전트별 필터링은 deepagents API에서 직접 지원하지 않음.

**PoC 전략**: `/skills/` 단일 소스로 모든 스킬 로드. 프로그레시브 디스클로저 방식이므로 에이전트는 필요한 스킬만 로드한다. 시스템 프롬프트에서 에이전트 연결 스킬 이름을 명시하여 가이드.

**향후 프로덕션 전략** (M3 스코프 아님):
- `FilteringFilesystemBackend` 구현 (ls_info 결과를 agent_skills 기반으로 필터링)
- 또는 per-agent 스킬 디렉토리 (`data/agents/{agent_id}/skills/`) + 파일 복사

### 3. Text 스킬 통합: 디스크 물질화

Text 스킬(DB `content` 필드만 존재, `storage_path` 없음)을 deepagents 네이티브 방식으로 통합하려면 디스크에 `SKILL.md` 파일이 필요하다.

**물질화 전략:**

```python
# skill_service.py — 스킬 생성/수정 시
def _materialize_skill_to_disk(skill: Skill) -> str:
    """text 스킬의 content를 data/skills/{id}/SKILL.md로 기록."""
    skill_dir = Path(settings.skills_data_dir) / str(skill.id)
    skill_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = f"---\nname: {skill.name}\ndescription: {skill.description or ''}\n---\n\n"
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(frontmatter + skill.content, encoding="utf-8")

    return str(skill_dir)
```

**실행 시점:**
- 스킬 **생성** 시: `create_skill()` → `_materialize_skill_to_disk()` → `storage_path` 설정
- 스킬 **수정** 시: `update_skill()` → `_materialize_skill_to_disk()` → SKILL.md 덮어쓰기
- **기존 text 스킬**: 서버 시작 시 또는 첫 사용 시 lazy 물질화

**DB 모델 변경:**
- `Skill.type` 필드: 그대로 유지 ("text" | "package"). UI 구분용.
- `Skill.storage_path`: text 스킬도 물질화 후 설정됨.
- `Skill.content`: **source of truth** 유지. 디스크 파일은 파생물.

### 4. Memory 경로 패턴

```
data/agents/{agent_id}/AGENTS.md
```

**파라미터 전달:**
```python
memory=[f"/agents/{agent_id}/AGENTS.md"]
```

**MemoryMiddleware 동작:**
1. `backend.download_files(["/agents/{agent_id}/AGENTS.md"])`
2. 파일 존재 시 → 콘텐츠를 시스템 프롬프트에 주입
3. 파일 미존재 시 → `file_not_found` → 무시 (에러 없음)

**디렉토리 생성 시점:**
- **에이전트 생성 시**: `agent_service.create_agent()` → `data/agents/{agent_id}/` 생성
- 빈 AGENTS.md 생성 (선택적) 또는 파일 없이 시작 → MemoryMiddleware가 무시
- 에이전트가 대화 중 Write 도구로 AGENTS.md 작성 가능

**AGENTS.md 초기 콘텐츠:**
```markdown
# Agent Memory

(에이전트가 학습한 내용이 여기에 기록됩니다)
```

### 5. `build_agent()` 시그니처 변경

```python
def build_agent(
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    *,
    middleware: list | None = None,
    checkpointer: Any | None = None,
    store: Any | None = None,
    backend: Any | None = None,
    skills: list[str] | None = None,       # ← 신규
    memory: list[str] | None = None,       # ← 신규
    name: str | None = None,
) -> Any:
    """Build a deep agent. Returns CompiledStateGraph."""
    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware or (),
        checkpointer=checkpointer,
        store=store,
        backend=backend,
        skills=skills,
        memory=memory,
        name=name,
    )
```

### 6. `execute_agent_stream()` 변경

```python
async def execute_agent_stream(
    # ... 기존 파라미터 ...
    agent_skills: list[dict] | None = None,   # ← 신규: [{skill_id, storage_path}]
    agent_id: str | None = None,               # ← 신규: 메모리 경로용
) -> AsyncGenerator[str, None]:
    # ... 기존 도구 생성 ...
    # skill_package 분기 제거

    # Backend 생성
    from deepagents.backends import FilesystemBackend
    backend = FilesystemBackend(
        root_dir=str(Path(__file__).resolve().parent.parent.parent / "data"),
        virtual_mode=True,
    )

    # Skills 소스 구성
    skills_sources: list[str] | None = None
    if agent_skills:
        skills_sources = ["/skills/"]

    # Memory 소스 구성
    memory_sources: list[str] | None = None
    if agent_id:
        memory_sources = [f"/agents/{agent_id}/AGENTS.md"]

    agent = build_agent(
        model,
        langchain_tools,
        system_prompt,
        middleware=middleware or None,
        checkpointer=get_checkpointer(),
        backend=backend,
        skills=skills_sources,
        memory=memory_sources,
        name=f"agent_{thread_id[:8]}",
    )
    # ... 기존 스트리밍 ...
```

### 7. 호출자 변경 (conversations.py)

```python
# 기존
system_prompt = build_effective_prompt(agent)
tools_config = build_tools_config(agent, str(conversation.id))

# 변경 후
system_prompt = agent.system_prompt  # build_effective_prompt 제거
tools_config = build_tools_config(agent, str(conversation.id))  # skill_package 제거됨
agent_skills = [
    {"skill_id": str(link.skill.id), "storage_path": link.skill.storage_path}
    for link in agent.skill_links
    if link.skill and link.skill.storage_path
]

async for chunk in execute_agent_stream(
    ...,
    agent_skills=agent_skills or None,
    agent_id=str(agent.id),
):
    yield chunk
```

---

## 대안

### 대안 A: CompositeBackend (StateBackend + FilesystemBackend)

```python
backend = CompositeBackend(
    default=StateBackend,
    routes={
        "/skills/": FilesystemBackend(root_dir="./data/skills", virtual_mode=True),
        "/agents/": FilesystemBackend(root_dir="./data/agents", virtual_mode=True),
    }
)
```

**장점**: 에이전트 스크래치패드(ephemeral) 제공
**단점**: StateBackend는 팩토리 패턴 필요 (`lambda rt: StateBackend(rt)`). 스크래치패드 사용 시나리오 없음. 불필요한 복잡성.
**판단**: 현재 스코프에서 스크래치패드 불필요 → 기각

### 대안 B: StoreBackend (PostgresStore)

```python
backend = CompositeBackend(
    default=StateBackend,
    routes={
        "/memories/": StoreBackend,
        "/skills/": FilesystemBackend(...),
    }
)
```

**장점**: 메모리가 DB에 저장되어 cross-thread 공유, 백업 용이
**단점**: StoreBackend는 namespace 기반 — AGENTS.md 파일 패턴과 호환 복잡. 추가 인프라(PostgresStore). MemoryMiddleware가 `download_files()` 사용 → StoreBackend 호환성 확인 필요.
**판단**: PoC 단계에서 과도한 복잡성 → 기각. 향후 프로덕션에서 재검토.

### 대안 C: skills 파라미터 미사용, 시스템 프롬프트 유지

**장점**: 변경 최소
**단점**: deepagents 프로그레시브 디스클로저 활용 불가. M3 목표 미달성.
**판단**: 목표와 불일치 → 기각

### 대안 D: Per-agent 심볼릭 링크

각 에이전트의 연결된 스킬만 `data/agents/{agent_id}/skills/` 디렉토리에 심볼릭 링크.

**장점**: 에이전트별 스킬 격리
**단점**: `FilesystemBackend`는 `O_NOFOLLOW` 플래그로 심볼릭 링크 추종 차단 (Linux/macOS). 실제로 동작하지 않음.
**판단**: 기술적 불가 → 기각

---

## 설계 상세

### 디렉토리 구조 변경

```
data/
├── skills/                        # 기존 유지
│   └── {skill_id}/
│       ├── SKILL.md               # frontmatter: name, description
│       ├── scripts/               # (package 스킬)
│       ├── references/
│       └── _outputs/
│
├── agents/                        # ← 신규
│   └── {agent_id}/
│       └── AGENTS.md              # 에이전트 장기 기억
│
└── conversations/                 # 기존 유지
    └── {conversation_id}/
```

### 제거 대상 코드

| 파일 | 제거 내용 | 이유 |
|------|-----------|------|
| `skill_tool_factory.py` | 전체 삭제 | deepagents SkillsMiddleware가 대체 |
| `skill_executor.py` | 전체 삭제 | 스크립트 실행은 에이전트 빌트인 도구로 대체 |
| `chat_service.py` | `build_effective_prompt()` 스킬 주입 로직 | SkillsMiddleware가 시스템 프롬프트 주입 담당 |
| `chat_service.py` | `build_tools_config()` skill_package 로직 | skill_package 도구 변환 불필요 |
| `executor.py` | `skill_package` 분기 | 도구 생성 대신 skills 파라미터 사용 |

### 유지 대상

| 항목 | 이유 |
|------|------|
| `Skill` DB 모델 | UI에서 스킬 CRUD 필요. `content` 필드는 source of truth |
| `AgentSkillLink` 모델 | 에이전트-스킬 연결 관리 |
| `skill_service.py` | 스킬 CRUD 서비스 (물질화 로직 추가) |
| `routers/skills.py` | 스킬 API 엔드포인트 |
| `schemas/skill.py` | API 스키마 |

### 데이터 흐름 (M3 이후)

```
POST /api/conversations/{id}/messages
│
├─ 1. maybe_set_auto_title(content)
├─ 2. get_agent_with_tools(agent_id)  [skill_links 포함]
├─ 3. system_prompt = agent.system_prompt  ← build_effective_prompt 제거
├─ 4. build_tools_config(agent)  ← skill_package 분기 제거
├─ 5. agent_skills = [linked package skills]
│
├─ 6. execute_agent_stream(
│       ..., agent_skills=agent_skills, agent_id=str(agent.id))
│    │
│    ├─ 6a. create_chat_model()
│    ├─ 6b. create tools (builtin/prebuilt/custom/mcp)  ← skill_package 제거
│    ├─ 6c. FilesystemBackend(root_dir=data/, virtual_mode=True)
│    ├─ 6d. build_agent(skills=["/skills/"], memory=["/agents/{id}/AGENTS.md"],
│    │       backend=backend, ...)
│    │    └─ create_deep_agent(skills=..., memory=..., backend=...)
│    │         ├─ SkillsMiddleware → before_agent: _list_skills("/skills/")
│    │         │   → 서브디렉토리 스캔 → SKILL.md 파싱 → 메타데이터 상태 저장
│    │         │   → wrap_model_call: 시스템 프롬프트에 스킬 목록 주입
│    │         └─ MemoryMiddleware → before_agent: download AGENTS.md
│    │             → wrap_model_call: 시스템 프롬프트에 메모리 콘텐츠 주입
│    └─ 6e. stream_agent_response()
│
└─ 7. StreamingResponse → Frontend (SSE)
```

### 스킬 생성/수정 흐름

```
POST /api/skills (create)  |  PUT /api/skills/{id} (update)
│
├─ skill_service.create_skill() / update_skill()
│   ├─ DB에 Skill 레코드 저장
│   └─ _materialize_skill_to_disk(skill)
│       ├─ data/skills/{skill.id}/ 디렉토리 생성
│       ├─ SKILL.md 작성 (frontmatter + content)
│       └─ skill.storage_path = str(skill_dir) 설정
│
└─ Response: SkillResponse
```

---

## 결과

### 긍정적
- **프로그레시브 디스클로저**: 스킬이 한 번에 로드되지 않고, 에이전트가 필요 시 탐색
- **코드 감소**: `skill_tool_factory.py` + `skill_executor.py` + 관련 로직 제거 (~150줄)
- **통합 경로**: text/package 구분 없이 모든 스킬이 SKILL.md 기반으로 통합
- **메모리 시스템**: 에이전트별 장기 기억(AGENTS.md) 지원
- **프레임워크 활용**: deepagents 네이티브 middleware 활용으로 유지보수 부담 감소

### 부정적
- **PoC 한계 — 스킬 격리 없음**: 모든 에이전트가 `/skills/` 전체를 탐색. 에이전트별 필터링은 프로그레시브 디스클로저 + 시스템 프롬프트 가이드에 의존
- **디스크 물질화 필요**: text 스킬을 디스크에 기록하는 추가 I/O. Content 필드와 SKILL.md 이중 관리 (DB가 source of truth)
- **스크립트 실행 방식 변경**: 기존 `skill_executor.py`의 격리된 Python 실행이 사라짐. 에이전트의 일반 도구로 대체되므로 보안 격리 약화 (PoC에서는 수용 가능)

### 향후 과제 (M3 이후)
- [ ] Per-agent 스킬 필터링 (FilteringFilesystemBackend 또는 custom middleware)
- [ ] 메모리 자동 관리 (요약, 정리, 만료)
- [ ] StoreBackend 전환 검토 (cross-instance 메모리 공유 필요 시)
- [ ] 스킬 스크립트 격리 실행 환경 (sandbox)
