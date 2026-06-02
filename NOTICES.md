# Third-party Notices

이 문서는 Moldy(natural-mold) 프로젝트가 포함하거나 참조하는 제3자 오픈소스 데이터, 패턴, 라이브러리의 출처와 라이선스 메모를 기록한다.

---

## 사용 라이브러리

다음 라이브러리는 표준 의존성으로, 자체 라이선스를 따른다.

- `cryptography` (BSD/Apache-2.0) — Cipher V2 구현
- `hvac` (Apache-2.0) — Vault SDK
- `authlib` 또는 `httpx-auth` — OAuth2 클라이언트
- `langchain`, `langgraph`, `langchain-mcp-adapters` (MIT) — AI 런타임
- `lucide-react` (ISC) — 프론트엔드 아이콘
- `shadcn/ui` (MIT) — 프론트엔드 컴포넌트

각 의존성은 `backend/pyproject.toml`, `frontend/package.json`에 명시.

---

## LiteLLM 데이터 카탈로그

`backend/app/data/litellm_model_catalog.json`는 [BerriAI/litellm](https://github.com/BerriAI/litellm)의 `model_prices_and_context_window.json` (MIT License) 스냅샷을 가공한 모델 가격/메타데이터 카탈로그이다.

| Moldy 파일 | 출처 | 라이선스 |
|---|---|---|
| `backend/app/data/litellm_model_catalog.json` | `BerriAI/litellm` `model_prices_and_context_window.json` | MIT |
| `backend/app/services/model_metadata.py` (`enrich_model`, `get_anthropic_models`) | 위 카탈로그를 lazy 파싱 | MIT (데이터) |
| `backend/app/services/model_test.py` (probe + clean error + curl reproduction 패턴) | LiteLLM proxy `/health/test_connection` 핸들러 + dashboard `model_connection_test.tsx` (MIT) | MIT |
| `backend/app/hooks/{base,registry}.py` (CustomLogger 인터페이스 패턴 — pre/post/failure hook) | LiteLLM `litellm/integrations/custom_logger.py` `CustomLogger` (MIT) | MIT |
| `backend/app/services/health_check.py` + `backend/app/routers/health.py` (per-target latest + history 엔드포인트 형태) | LiteLLM proxy `/health/history` (MIT) | MIT |
| `backend/app/services/spend_writer.py` (`DailySpendUpdateQueue` — async queue + batch UPSERT 패턴) | LiteLLM `litellm/proxy/db/db_transaction_queue/daily_spend_update_queue.py` + `db_spend_update_writer.py` (MIT) | MIT |
| `backend/app/services/usage_aggregate.py` + `backend/app/routers/usage.py::/api/usage/daily` (per-axis daily aggregate read API 형태) | LiteLLM `LiteLLM_DailyUserSpend` / `LiteLLM_DailyTeamSpend` 등 daily aggregate 패턴 (MIT) | MIT |
| `backend/app/agent_runtime/model_factory.py::create_chat_model_with_fallback` (primary → fallback 체인 walk + recoverable error 분기) | LiteLLM `litellm/router_utils/fallback_event_handlers.py` + `Router.async_function_with_fallbacks` (MIT) | MIT |

LiteLLM은 LLM provider별 토큰 비용·컨텍스트 윈도우·기능 플래그를 한 JSON으로 유지·관리한다. Moldy의 모델 디스커버리는 provider 응답에 메타가 누락된 경우(예: OpenAI `/v1/models`는 가격을 노출하지 않음) 위 카탈로그로 보강한다. 모델 테스트 surface(`run_model_test`)는 LiteLLM 프록시의 `test_model_connection` 워크플로 및 dashboard의 clean-error/curl 생성 UX 패턴만 차용했으며, 코드는 직접 복사하지 않고 식별자/문자열은 모두 Moldy 도메인에 맞춰 자체화했다.

`app/hooks/`의 cross-cutting hook 프레임워크(`CustomHook` ABC, `HookRegistry`의 `run_pre`/`run_post`/`run_failure` 디스패치)는 LiteLLM `CustomLogger` 인터페이스의 lifecycle 컨벤션만 차용했다. 코드는 직접 복사하지 않았으며 클래스명·메서드명·필드명·메타데이터 키는 모두 Moldy 도메인 규약에 맞춰 자체 명명했다. Health check `/api/health/{models,mcp-servers,history,check}` 라우트와 시계열 history 테이블 형태 역시 LiteLLM proxy `/health/history`의 응답 구조 아이디어만 차용한 자체 구현이다.

`app/services/spend_writer.py`의 `DailySpendUpdateQueue`(asyncio.Queue + 백그라운드 배치 flush + `(date, target_id)` UPSERT)는 LiteLLM의 동명 큐 + `db_spend_update_writer`의 batch commit 패턴만 차용했다. 데이터클래스 시그니처(`SpendEntry`), per-axis aggregate 테이블 명세, dialect-aware UPSERT 구문은 모두 SQLAlchemy 2.x 기반의 자체 구현이다. `/api/usage/daily` 라우트와 `usage_aggregate.get_daily_spend()`의 (target_kind/target_id/from/to/group_by) 파라미터 형태는 LiteLLM dashboard의 daily-usage 엔드포인트 응답 구조에서 영감을 받았으나, tenancy 가드(mock_user FK)와 SQL 쿼리 본체는 모두 자체 작성이다.

`app/agent_runtime/model_factory.py::create_chat_model_with_fallback`의 primary → fallback 체인 walk + recoverable error 분기 패턴은 LiteLLM Router의 fallback 동작 방식만 차용했다. 호출 시그니처(`agent`/`db` 인자), audit log action(`fallback`)·메타데이터 키, 회복 가능 status code 셋, agent.model_fallback_list JSON 컬럼 형태는 모두 Moldy 도메인 자체 정의다.

---

## 다중 소스 모델 카탈로그 파이프라인 (M11)

Moldy의 모델 메타데이터는 여러 공개 데이터셋에서 6시간 주기로 자동 수집되며, 3-layer merge(provider 기본값 → model 기본값 → provider-specific override)로 통합된 단일 `catalog.json`을 빌드한다. 파이프라인 구성(loader/normalize/merge/resolve 분리, 사파스/additive null inheritance, JSON Schema 검증)은 ENTERPILOT/ai-model-list 프로젝트의 형태를 차용했다. 코드는 직접 복사하지 않았으며, 식별자·정규화 규칙·CI 와이어링·에러 처리는 모두 Moldy 도메인에 맞춰 자체 작성됐다.

### Upstream 데이터 소스

| 소스 | URL | 라이선스 | 비고 |
|---|---|---|---|
| LiteLLM | `ENTERPILOT/ai-model-price-list/sources/litellm_model_prices.json` | MIT | USD per token |
| OpenRouter | `ENTERPILOT/ai-model-price-list/sources/openrouter_models.json` | Unspecified | USD per token (string) |
| llm-prices | `ENTERPILOT/ai-model-price-list/sources/llm_prices_current.json` | Unspecified | USD per million tokens |
| pydantic genai-prices | `ENTERPILOT/ai-model-price-list/sources/pydantic_genai_prices.json` | MIT | tier/constraint 지원 |

`ENTERPILOT/ai-model-price-list`는 6시간마다 위 4개 데이터셋을 자동 미러링한다. Moldy는 raw 파일을 그대로 가져와 `backend/app/data/model_catalog/sources/`에 보관한다.

### 차용 패턴

| Moldy 파일 | 출처 | 라이선스 |
|---|---|---|
| `backend/app/services/model_catalog/loaders.py` (snapshot fetch + atomic write + sha256 metadata) | `ai-model-list/scripts/fetch_sources.py` 패턴 | (자체 작성, 패턴만 차용) |
| `backend/app/services/model_catalog/normalize.py` (per-source → ModelEntry) | `ai-model-list/pipeline/normalize.py` 패턴 | (자체 작성, 패턴만 차용) |
| `backend/app/services/model_catalog/merge.py` (3-layer merge + sparse/additive) | `ai-model-list/pipeline/{resolve,render}.py` 패턴 | (자체 작성, 패턴만 차용) |
| `backend/app/services/model_catalog/resolve.py` (provider → model → provider_model walk) | `ai-model-list` README "Override / Merge Order" 절 | (자체 작성, 패턴만 차용) |
| `backend/app/services/model_catalog/validate.py` (JSON Schema gate) | `ai-model-list/scripts/validate.py` | (자체 작성, 패턴만 차용) |
| `backend/app/data/model_catalog/schema.json` | `ai-model-list/schema.json` (구조 단순화) | (자체 작성) |
| `backend/app/services/model_catalog_updater.py` (cron-triggered build) | `ai-model-list/scripts/build_registry.py` 흐름 | (자체 작성, 패턴만 차용) |

`backend/app/data/model_catalog/sources/*.json`은 ENTERPILOT/ai-model-price-list에서 가져온 upstream 스냅샷이며, 각 소스의 원본 라이선스(위 표)를 따른다. 가공 결과인 `catalog.json`은 Moldy 도메인 명명·정규화 규칙으로 빌드된 자체 산출물이다.

---

## MCP Server Catalog

`backend/app/data/mcp_server_registry.json`는 잘 알려진 MCP 서버(GitHub, Linear, Jira, Slack, Notion 등)의 wire 설정(transport, URL, stdio command, env_var template)을 미리 큐레이팅한 정적 카탈로그이다. 사용자가 "Add from registry" 흐름에서 즉시 정상 작동하는 `McpServer` row를 만들 수 있게 한다.

| Moldy 파일 | 출처 | 라이선스 |
|---|---|---|
| `backend/app/data/mcp_server_registry.json` | 각 MCP 서버 공식 문서/저장소를 참조해 직접 큐레이팅 | 자체 작성 |
| `backend/app/services/mcp_registry.py` (curated MCP 카탈로그 패턴) | "connector library" 패턴 (출처: 일반 SaaS 통합 카탈로그 관행) | 패턴만 차용 |

각 entry의 transport/url/command 값은 해당 MCP 서버 공식 문서(`documentation_url` 필드)에 기재된 권장 접속 정보를 따랐다. 카탈로그 자체는 Moldy 자체 작성물이다.
