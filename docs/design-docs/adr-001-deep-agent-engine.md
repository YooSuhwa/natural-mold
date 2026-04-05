# ADR-001: Deep Agent 엔진 교체

## 상태: 제안됨

## 맥락

Moldy의 AI 에이전트 실행 엔진은 현재 두 가지 경로로 에이전트를 생성한다:

1. `langchain.agents.create_agent` — 미들웨어 지원, ImportError 시 폴백
2. `langgraph.prebuilt.create_react_agent` — 폴백, 미들웨어 무시

MCP 도구는 `mcp_client.py`에서 HTTP/StreamableHTTP 프로토콜을 직접 구현하여 호출한다.
이 구조의 문제점:

- **이중 경로**: create_agent / create_react_agent 폴백으로 동작 예측이 어려움
- **MCP 직접 구현**: 프로토콜 변경 시 자체 유지보수 부담
- **도구 이름 충돌**: chat_service.py에서 수동으로 중복 해소 로직 관리

`deepagents` 패키지의 `create_deep_agent()`와 `langchain-mcp-adapters`의 `MultiServerMCPClient`를 도입하여 단일 경로로 통합한다.

---

## 결정

### 1. `build_agent()` → `create_deep_agent()` 교체

**현재 시그니처** (`executor.py:27-57`):
```python
def build_agent(
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    middleware: list | None = None,
) -> Any:
    # create_agent 시도 → ImportError 시 create_react_agent 폴백
```

**새 시그니처**:
```python
def build_agent(
    model: BaseChatModel,
    tools: list[BaseTool],
    system_prompt: str,
    middleware: list | None = None,
    checkpointer: Checkpointer | None = None,
) -> CompiledStateGraph:
    from deepagents import create_deep_agent

    return create_deep_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        middleware=middleware or [],
        checkpointer=checkpointer,
    )
```

**변경 사항:**
- `create_agent` + `create_react_agent` 이중 경로 제거
- 단일 `create_deep_agent` 호출
- `checkpointer` 파라미터 추가 (LangGraph 체크포인트 통합)
- 반환 타입을 `CompiledStateGraph`로 명시 (기존과 동일한 인터페이스)
- `middleware`는 빈 리스트로 기본값 처리 (None 분기 제거)

### 2. MCP 도구 생성: `create_mcp_tool()` → `MultiServerMCPClient`

**현재 방식** (`executor.py:93-104`, `tool_factory.py:319+`):
```python
# executor.py — 개별 MCP 도구마다 create_mcp_tool() 호출
elif tc.get("type") == "mcp" and tc.get("mcp_server_url"):
    tool = create_mcp_tool(name, description, mcp_server_url, mcp_tool_name, auth_config, ...)
    langchain_tools.append(tool)
```

각 MCP 도구가 개별 HTTP 호출로 실행됨. `call_mcp_tool()`이 매 호출마다 `streamablehttp_client()` 세션을 열고 닫음.

**새 방식**:
```python
# executor.py — execute_agent_stream() 내
mcp_servers = {}
for tc in tools_config:
    if tc.get("type") == "mcp" and tc.get("mcp_server_url"):
        server_url = tc["mcp_server_url"]
        headers = _auth_config_to_headers(tc.get("auth_config"))
        server_key = _url_to_server_key(server_url)
        mcp_servers[server_key] = {
            "transport": "streamable_http",
            "url": server_url,
            "headers": headers,
        }

if mcp_servers:
    from langchain_mcp_adapters import MultiServerMCPClient

    async with MultiServerMCPClient(mcp_servers, tool_name_prefix=True) as client:
        mcp_tools = await client.get_tools()
        langchain_tools.extend(mcp_tools)
```

**변경 사항:**
- `create_mcp_tool()` 제거 — 개별 도구 생성 불필요
- `_build_args_schema()` 제거 — `get_tools()`가 자동 스키마 생성
- `call_mcp_tool()` 제거 — 도구 실행이 LangChain Tool 인터페이스로 자동 처리
- `tool_name_prefix=True` — 서버별 접두사로 이름 충돌 자동 해소
- `auth_config` → HTTP `headers`로 변환하는 헬퍼 필요

### 3. `chat_service.py` MCP 이름 중복 해소 로직 제거

**현재** (`chat_service.py:201-214`):
```python
# Disambiguate duplicate MCP tool names by adding server prefix
name_counts: dict[str, int] = {}
for tc in tools_config:
    if tc.get("type") == "mcp":
        ...
```

**변경**: 이 블록 전체 삭제. `MultiServerMCPClient`의 `tool_name_prefix=True`가 동일 기능 제공.

### 4. `execute_agent_stream()` 시그니처 — 변경 없음

```python
async def execute_agent_stream(
    provider, model_name, api_key, base_url,
    system_prompt, tools_config, messages_history, thread_id,
    model_params=None, middleware_configs=None,
) -> AsyncGenerator[str, None]:
```

외부 호출자(`conversations.py`, `trigger_executor.py`)에 영향 없음. 내부 구현만 변경.

---

## 대안

### 옵션 A: create_deep_agent 전면 교체 (선택)

- **장점**: 단일 경로, 미들웨어 네이티브 지원, checkpointer 통합, 코드 단순화
- **단점**: deepagents 패키지 의존성 추가, API 변경 시 추종 필요

### 옵션 B: create_agent만 업그레이드 (기각)

- **장점**: 변경 최소화
- **단점**: create_react_agent 폴백 여전히 존재, MCP 직접 구현 유지, 미들웨어 통합 불완전

### 옵션 C: LangGraph functional API 직접 사용 (기각)

- **장점**: 최대 유연성
- **단점**: 보일러플레이트 대폭 증가, 미들웨어 수동 통합 필요

---

## 변경 파일 요약

| 파일 | 변경 유형 | 상세 |
|------|-----------|------|
| `executor.py` | **수정** | `build_agent()` 내부 → `create_deep_agent()`. MCP 도구 루프 → `MultiServerMCPClient` async context. |
| `tool_factory.py` | **삭제(부분)** | `create_mcp_tool()`, `_build_args_schema()` 제거. builtin/prebuilt/custom 유지. |
| `mcp_client.py` | **삭제(부분)** | `call_mcp_tool()`, `_extract_text()` 제거. `test_mcp_connection()`, `list_mcp_tools()` 유지 (MCP 서버 등록 UI용). |
| `chat_service.py` | **삭제(부분)** | MCP 이름 중복 해소 블록 (~14줄) 삭제. |
| `pyproject.toml` | **수정** | `deepagents`, `langchain-mcp-adapters` 의존성 추가. |

### 유지되는 모듈 (변경 없음)

| 파일 | 이유 |
|------|------|
| `streaming.py` | `create_deep_agent`도 `CompiledStateGraph` 반환 → `astream()` 동일 |
| `model_factory.py` | LLM 인스턴스 생성은 엔진 독립적 |
| `middleware_registry.py` | `create_deep_agent`의 `middleware` 파라미터로 그대로 전달 |
| `message_utils.py` | 메시지 포맷 변환 — 엔진 독립적 |
| `trigger_executor.py` | `execute_agent_stream()` 시그니처 유지 |
| `conversations.py` | `execute_agent_stream()` 시그니처 유지 |

---

## 새 헬퍼 함수 (executor.py 내부)

```python
def _auth_config_to_headers(auth_config: dict | None) -> dict[str, str]:
    """auth_config를 HTTP 헤더로 변환.
    
    지원 패턴:
    - {"api_key": "..."} → {"Authorization": "Bearer ..."}
    - {"jwt_token": "..."} → {"Authorization": "Bearer ..."}
    - {"headers": {...}} → 그대로 반환
    """
    if not auth_config:
        return {}
    if "headers" in auth_config:
        return auth_config["headers"]
    token = auth_config.get("api_key") or auth_config.get("jwt_token")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _url_to_server_key(url: str) -> str:
    """MCP 서버 URL을 고유 키로 변환."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.netloc.replace(".", "_").replace(":", "_")
```

---

## execute_agent_stream() 새 내부 구조

```python
async def execute_agent_stream(...) -> AsyncGenerator[str, None]:
    model = create_chat_model(provider, model_name, api_key, base_url, **(model_params or {}))

    langchain_tools: list[BaseTool] = []
    mcp_servers: dict[str, dict] = {}

    for tc in tools_config:
        match tc.get("type"):
            case "builtin":
                langchain_tools.append(create_builtin_tool(tc["name"]))
            case "prebuilt":
                langchain_tools.append(create_prebuilt_tool(tc["name"], auth_config=tc.get("auth_config")))
            case "custom" if tc.get("api_url"):
                langchain_tools.append(create_tool_from_db(...))
            case "mcp" if tc.get("mcp_server_url"):
                # MCP 도구는 서버별로 수집 → MultiServerMCPClient로 일괄 생성
                server_key = _url_to_server_key(tc["mcp_server_url"])
                mcp_servers[server_key] = {
                    "transport": "streamable_http",
                    "url": tc["mcp_server_url"],
                    "headers": _auth_config_to_headers(tc.get("auth_config")),
                }
            case "skill_package":
                langchain_tools.extend(create_skill_tools(...))

    middleware = build_middleware_instances(middleware_configs or [])
    middleware += get_provider_middleware(provider)

    # MCP 도구가 있으면 async context 안에서 에이전트 실행
    if mcp_servers:
        from langchain_mcp_adapters import MultiServerMCPClient

        async with MultiServerMCPClient(mcp_servers, tool_name_prefix=True) as client:
            mcp_tools = await client.get_tools()
            langchain_tools.extend(mcp_tools)
            agent = build_agent(model, langchain_tools, system_prompt, middleware=middleware or None)
            lc_messages = convert_to_langchain_messages(messages_history)
            config = {"configurable": {"thread_id": thread_id}}
            async for chunk in stream_agent_response(agent, lc_messages, config):
                yield chunk
    else:
        agent = build_agent(model, langchain_tools, system_prompt, middleware=middleware or None)
        lc_messages = convert_to_langchain_messages(messages_history)
        config = {"configurable": {"thread_id": thread_id}}
        async for chunk in stream_agent_response(agent, lc_messages, config):
            yield chunk
```

**핵심**: `MultiServerMCPClient`는 async context manager로, MCP 도구가 있을 때만 활성화. 에이전트 실행이 context 내에서 완료되어야 MCP 연결이 유지된다.

---

## 결과

- **단순화**: 에이전트 생성 경로 2개 → 1개
- **MCP 안정성**: 직접 구현 → 공식 어댑터, 커넥션 풀링, 자동 스키마
- **이름 충돌**: 수동 해소 로직 → `tool_name_prefix` 자동 처리
- **코드 제거량**: ~100줄 삭제 (`create_mcp_tool`, `_build_args_schema`, `call_mcp_tool`, 중복 해소 블록)
- **외부 API 변경 없음**: `execute_agent_stream()` 시그니처 유지
- **리스크**: `deepagents` 패키지 안정성, `MultiServerMCPClient`의 streamable_http 지원 확인 필요
