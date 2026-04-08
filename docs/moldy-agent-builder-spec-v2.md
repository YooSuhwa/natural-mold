# Deep Agent Builder — 시스템 기획서 (v2)

> 자연어 요청 하나로 맞춤형 AI 에이전트를 자동 생성하고 관리하는 멀티 에이전트 오케스트레이션 시스템
>
> **v2 변경점:** Deep Agent Assistant 프롬프트 분석을 통해 도구 생태계, 시스템 프롬프트 템플릿, 에이전트 관리 워크플로우, 서브에이전트 구조, 시크릿 관리, 크론 스케줄, RAG 설정 등의 실제 구현 세부사항을 추가함.

---

## 1. 프로젝트 개요

### 1.1 시스템 전체 구조: 빌더 + 어시스턴트

이 시스템은 두 개의 독립적인 에이전트로 구성된다:

| 에이전트 | 역할 | 작동 시점 |
|---------|------|----------|
| **Deep Agent Builder** | 자연어 요청을 받아 새 에이전트를 **처음부터 생성**하는 오케스트레이터 | 에이전트 생성 시 |
| **Deep Agent Assistant** | 이미 생성된 에이전트의 설정을 **수정·관리**하는 도우미 | 에이전트 생성 후 |

Builder가 에이전트를 만들면, 이후 사용자는 Assistant를 통해 도구 추가/제거, 시스템 프롬프트 개선, 모델 변경, 스케줄 설정 등을 할 수 있다.

### 1.2 핵심 설계 원칙

- **단일 책임 원칙 (Single Responsibility):** 각 서브에이전트는 정확히 하나의 전문 영역만 담당한다.
- **순차적 파이프라인:** 이전 단계의 출력이 다음 단계의 입력이 되는 체인 구조.
- **파일 기반 상태 관리:** 모든 중간 산출물을 YAML/Markdown 파일로 디스크에 기록하여 재현성과 디버깅 용이성을 확보한다.
- **격리된 컨텍스트:** 서브에이전트는 서로의 내부 상태를 공유하지 않으며, 오직 구조화된 데이터만 전달받는다.
- **VERIFY before MODIFY:** 모든 수정 작업 전에 현재 상태를 먼저 확인한다 (Assistant 원칙에서 차용).
- **MINIMAL changes:** 사용자가 명시적으로 요청한 부분만 수정한다.

### 1.3 시스템 구성 요소

| 구성 요소 | 역할 |
|-----------|------|
| **오케스트레이터 (Deep Agent Builder)** | 전체 빌드 파이프라인을 순서대로 실행하고, 서브에이전트 호출·결과 저장·진행 상황 보고를 담당 |
| **의도 분석 서브에이전트** | 사용자 요청을 정밀 분석하여 AgentCreationIntent 구조체로 정리 |
| **도구 추천 서브에이전트** | Intent를 기반으로 에이전트에 필요한 도구(Tool) 목록을 선정 |
| **미들웨어 추천 서브에이전트** | Intent + 도구 정보를 기반으로 미들웨어(안정성·성능·보안 계층)를 선정 |
| **프롬프트 생성 서브에이전트** | 위 모든 정보를 종합하여 에이전트의 시스템 프롬프트(마크다운)를 작성 |
| **빌드 시스템** | 설정 파일들을 읽어 실제 LangGraph/Agent Framework 기반 에이전트를 인스턴스화 |
| **Deep Agent Assistant** | 빌드 완료된 에이전트의 설정을 수정·관리하는 별도 에이전트 |

---

## 2. 전체 아키텍처

### 2.1 에이전트 생성 흐름 (Builder)

```
사용자 요청 (자연어)
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  오케스트레이터 (Deep Agent Builder)                              │
│                                                                  │
│  Phase 1: 프로젝트 초기화                                         │
│    ├─ write_project_config()   → project_config.md               │
│    ├─ create_project_folder()  → 프로젝트 디렉토리 생성            │
│    └─ update_project_config_path() → project_config.md 경로 갱신  │
│                                                                  │
│  Phase 2: 의도 분석                                               │
│    └─ 서브에이전트 호출 (description + 사용자 요청)                 │
│       → AgentCreationIntent (JSON)                               │
│                                                                  │
│  Phase 3: 도구 추천                                               │
│    └─ 서브에이전트 호출 (AgentCreationIntent)                      │
│       → tools.yaml 저장                                          │
│                                                                  │
│  Phase 4: 미들웨어 추천                                           │
│    └─ 서브에이전트 호출 (AgentCreationIntent + 도구 목록)           │
│       → middlewares.yaml 저장                                    │
│                                                                  │
│  Phase 5: 시스템 프롬프트 생성                                     │
│    └─ 서브에이전트 호출 (Intent + Tools + Middlewares)              │
│       → system_prompt.md 저장                                    │
│                                                                  │
│  Phase 6: 에이전트 설정 저장                                       │
│    └─ write_agent_config() → config.yaml                         │
│                                                                  │
│  Phase 7: 최종 에이전트 빌드                                       │
│    └─ build_final_agent() → Agent ID 반환                        │
└──────────────────────────────────────────────────────────────────┘
    │
    ▼
실제 동작하는 에이전트 (agent_id로 호출 가능)
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│  Deep Agent Assistant (생성 후 관리)                              │
│  - 도구/미들웨어/서브에이전트 추가·제거                             │
│  - 시스템 프롬프트 수정·개선                                       │
│  - 모델 설정 변경                                                 │
│  - 크론 스케줄 관리                                               │
│  - RAG 파일 설정                                                  │
│  - 시크릿(API 키) 확인                                            │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 의존성 그래프 (Builder 내부)

```
Phase 1 ──→ Phase 2 ──→ Phase 3 ──┐
                                   │
                                   ▼
                                Phase 4
                                   │
                         ┌─────────┘
                         ▼
                     Phase 5 ──→ Phase 6 ──→ Phase 7
```

Phase 3(도구 추천)과 Phase 4(미들웨어 추천)는 둘 다 Phase 2의 AgentCreationIntent에만 의존하지만, Phase 4가 도구 목록을 참조하면 더 정확한 추천이 가능하므로 기본적으로 Phase 3 → Phase 4 순차 실행을 권장한다.

---

## 3. 실제 도구·미들웨어·서브에이전트 생태계

> Deep Agent Assistant 프롬프트에서 확인된 실제 리소스 관리 체계

### 3.1 도구(Tool) 관리 체계

에이전트에 사용 가능한 도구는 중앙 카탈로그에서 관리된다. `list_available_tools`로 조회 가능하며, `add_tool_to_agent` / `remove_tool_from_agent`로 에이전트에 연결·해제한다.

**확인된 도구 예시:**

| 카테고리 | 도구명 | 설명 |
|---------|--------|------|
| 웹 검색 | tavily_search | 일반 웹 검색, 최신 뉴스/정보에 적합 |
| 시맨틱 검색 | exa_search | 개념/문맥 기반 시맨틱 검색 |
| 한국 뉴스 | naver_news | 네이버 뉴스 검색 API |
| 한국 블로그 | naver_blog | 네이버 블로그 검색 API |

**내부 자동 도구 (모든 에이전트에 자동 포함, 카탈로그에 표시 안 됨):**

| 도구명 | 설명 |
|--------|------|
| list_agent_files | 에이전트에 업로드된 영구 파일 목록 조회 |
| read_agent_file | 파일 내용 읽기 (PDF→Markdown, Image→Base64) |

**도구 설정 구조:**
각 도구는 개별 설정(config_override)을 가질 수 있으며, `get_tool_config` / `update_tool_config`으로 관리된다. 또한 도구마다 필요한 시크릿(API 키)이 정의되어 있다.

### 3.2 미들웨어(Middleware) 관리 체계

미들웨어는 에이전트의 도구 호출 전후에 개입하는 계층이다. `list_available_middlewares`로 조회, `add_middleware_to_agent` / `remove_middleware_from_agent`로 관리한다.

**확인된 미들웨어 예시:**

| 미들웨어명 | 설명 | 특이사항 |
|-----------|------|---------|
| ToolRetryMiddleware | 외부 API 호출 실패 시 자동 재시도 | - |
| SummarizationMiddleware | 대화 히스토리 자동 요약으로 토큰 절약 | - |
| TodoListMiddleware | 작업 계획 수립 및 진행 추적 | **시스템 프롬프트에 사용 지침 추가 필수** (아래 참조) |

**TodoListMiddleware 필수 프롬프트 지침:**

이 미들웨어를 추가할 때는 반드시 다음 내용을 시스템 프롬프트에 포함해야 한다:

```markdown
## 작업 계획 및 실행 (Todo List)

복잡한 작업을 수행할 때는 반드시 `write_todos` 도구를 활용하여 
작업 계획(plans)을 먼저 수립하세요.
계획을 세운 후, 각 항목을 순차적으로 이행하면서 작업을 수행합니다.

### 작업 순서
1. 사용자 요청을 분석하여 필요한 단계를 파악
2. `write_todos` 도구로 작업 계획을 작성
3. 계획에 따라 각 단계를 순차적으로 실행
4. 각 단계 완료 시 진행 상황을 업데이트
```

### 3.3 서브에이전트(Subagent) 관리 체계

에이전트는 다른 에이전트를 서브에이전트로 호출할 수 있다. `list_available_subagents`로 조회, `add_subagent_to_agent` / `remove_subagent_from_agent`로 관리한다.

서브에이전트 추가 시 확인할 사항:
- 역할/전문 분야
- 사용할 모델
- 호출 조건 (언제 위임할 것인가)
- 접근 가능한 도구

### 3.4 모델(Model) 설정

`list_available_models`로 사용 가능한 모델 목록을 조회하고, `update_model_config`로 설정을 변경한다.

**설정 가능한 파라미터:**

| 파라미터 | 설명 | 기본값 |
|---------|------|--------|
| model_name | LLM 모델 식별자 | anthropic:claude-sonnet-4-5 |
| temperature | 응답의 창의성/무작위성 | (모델 기본값) |
| max_tokens | 최대 응답 토큰 수 | (모델 기본값) |
| top_p | 누적 확률 샘플링 | (모델 기본값) |
| top_k | 상위 K개 토큰 샘플링 | (모델 기본값) |

### 3.5 시크릿(Secret) 관리

에이전트가 외부 API를 사용하려면 API 키가 필요하다. 시크릿 관리 체계:

1. `get_agent_required_secrets` → 에이전트에 필요한 시크릿 키 목록 조회 (모델, 도구, 미들웨어에서 필요한 키)
2. `get_user_secrets` → 사용자가 등록한 시크릿 목록 조회
3. 비교하여 누락된 키 식별
4. 누락된 키가 있으면 발급 가이드 제공 후 `/secrets` 페이지로 안내

### 3.6 크론 스케줄(Cron Schedule) 관리

에이전트에 예약 실행을 설정할 수 있다.

**스케줄 유형:**
- **반복(recurring):** cron 표현식으로 정의 (5필드: minute hour day-of-month month day-of-week)
- **1회(one-time):** scheduled_at으로 특정 시점 지정

**제약 사항:**
- 기본 타임존: Asia/Seoul
- 사용자당 최대 20개 스케줄 (모든 에이전트 합산)
- 1회 스케줄: scheduled_at은 미래 시점이어야 함

**주요 크론 패턴:**

| 패턴 | 표현식 | 설명 |
|------|--------|------|
| 매시 정각 | `0 * * * *` | Every hour |
| 매일 오전 9시 | `0 9 * * *` | Daily 9 AM |
| 평일 오전 9시 | `0 9 * * 1-5` | Weekdays 9 AM |
| 매주 월요일 10시 | `0 10 * * 1` | Every Monday 10 AM |
| 매월 1일 9시 | `0 9 1 * *` | 1st of month 9 AM |
| 30분마다 | `*/30 * * * *` | Every 30 minutes |

### 3.7 Recursion Limit (재귀 한도)

LangGraph 기반 에이전트의 실행 깊이를 제한하는 설정이다.

| 범위 | 권장 대상 |
|------|----------|
| 기본값 25 | 단순 Q&A |
| 25~50 | 일반 도구 사용 |
| 50~75 | 복잡한 분석 |
| 75~100 | 다단계 작업 |
| 100+ | 서브에이전트를 호출하는 에이전트 |

주의: 값이 높으면 무한 루프 발생 시 API 비용이 크게 증가할 수 있다.

---

## 4. 프로젝트 폴더 구조

### 4.1 데이터베이스 루트 구조

```
agent_database/
└── {user_id}/
    ├── project_config.md          # 사용자 메타데이터
    ├── tmp/                       # 프로젝트 작업 폴더
    │   └── {timestamp}_{uuid}/    # 각 에이전트 생성 세션
    └── agents/
        └── {agent_id}/            # 빌드 완료된 에이전트
            ├── project_config.md
            ├── system_prompt.md
            ├── tools.yaml
            ├── middlewares.yaml
            └── config.yaml
```

### 4.2 각 파일의 스키마

**project_config.md**
```markdown
# Project Configuration

user_id="{사용자 고유 ID}"
project_path="{user_id}/tmp/{timestamp}_{uuid}"
```

**tools.yaml**
```yaml
tools:
  - tool_name: "tavily_search"
    tool_path: "tools/tavily-search.yaml"
  - tool_name: "naver_news"
    tool_path: "tools/naver-news.yaml"
```

**middlewares.yaml**
```yaml
middlewares:
  - middleware_name: "ToolRetryMiddleware"
    middleware_path: "middlewares/tool-retry.yaml"
  - middleware_name: "SummarizationMiddleware"
    middleware_path: "middlewares/summarization.yaml"
```

**config.yaml**
```yaml
agent_name: "Web Search Agent"
agent_description: "사용자의 검색 쿼리를 받아 인터넷에서 정보를 검색하고..."
tools:
  - "tavily_search"
  - "naver_news"
  - "naver_blog"
middlewares:
  - "ToolRetryMiddleware"
  - "SummarizationMiddleware"
model_name: "anthropic:claude-sonnet-4-5"
primary_task_type: "web_search"
use_cases:
  - "정보 검색"
  - "뉴스 조회"
```

---

## 5. Phase별 상세 설계 (Builder)

---

### 5.1 Phase 1: 프로젝트 초기화

#### 목적
에이전트 생성 세션을 위한 작업 공간을 준비한다.

#### 실행 주체
오케스트레이터가 직접 도구를 호출한다. 서브에이전트(LLM)를 사용하지 않는다.

#### 단계별 동작

**Step 1 — write_project_config**
- 기능: 사용자 ID를 파일 시스템에 기록.
- 서브에이전트는 messages만 받으므로 state.user_id에 접근할 수 없다. 따라서 이 도구로 먼저 user_id를 파일에 기록해야 한다.
- 출력: `{AGENT_BASE_FOLDER}/{user_id}/project_config.md`
- 초기 내용: `user_id="{user_id}"`, `project_path=""`
- 제약: 반드시 create_project_folder보다 먼저 호출.

**Step 2 — create_project_folder**
- 기능: `{AGENT_BASE_FOLDER}/{user_id}/tmp/{timestamp}_{uuid}/` 폴더 생성.
- 타임스탬프 + UUID v4로 고유 식별자 보장.
- 출력: 생성된 폴더의 절대 경로.

**Step 3 — update_project_config_path**
- 기능: Step 1에서 만든 project_config.md의 빈 project_path를 Step 2의 경로로 갱신.

#### 오케스트레이터 지침

```
1. write_project_config 호출 → project_config.md 생성
2. create_project_folder 호출 → 프로젝트 폴더 생성
3. update_project_config_path 호출 → 경로 동기화
4. Todo에서 Phase 1 완료 표시
5. "[Phase 1 완료] 프로젝트 초기화 완료" 보고
```

---

### 5.2 Phase 2: 의도 분석

#### 목적
사용자의 자연어 요청을 분석하여 **AgentCreationIntent**를 생성한다.

#### 실행 주체
**의도 분석 서브에이전트**

#### 서브에이전트 시스템 프롬프트 (설계 참고용)

```markdown
# 의도 분석 에이전트 — 시스템 프롬프트

## 역할
당신은 AI 에이전트 생성을 위한 의도 분석 전문가입니다.
사용자의 자연어 요청을 받아 에이전트를 만들기 위해 필요한 모든 정보를
체계적으로 분석하고 구조화합니다.

## 출력 형식 (AgentCreationIntent)
반드시 아래 JSON 형식으로만 응답한다:

{
  "agent_name": "영문 에이전트 이름",
  "agent_name_ko": "한글 에이전트 이름",
  "agent_description": "에이전트의 역할과 기능에 대한 상세 설명 (3~5문장)",
  "primary_task_type": "에이전트의 핵심 작업을 한 문장으로 기술",
  "tool_preferences": "선호하는 도구 유형이나 API 종류",
  "output_style": "결과물의 형태 (요약, 리포트, 목록 등)",
  "response_tone": "응답의 톤과 스타일",
  "use_cases": ["사용 사례 1", "사용 사례 2", "사용 사례 3"],
  "constraints": ["제약 조건"],
  "required_capabilities": ["필수 기능 1", "필수 기능 2"]
}

## 추론 가이드라인
- "검색 에이전트"만 언급 → 일반 웹 검색 + 뉴스 검색을 기본 포함
- "번역 에이전트"만 언급 → 다국어 번역 기본, 한국어↔영어 우선
- "코딩 에이전트"만 언급 → 코드 생성 + 디버깅 + 설명 기본
- 톤 미명시 → "친근하고 캐주얼한 어조" 기본값
- output_style 미명시 → "간단한 요약과 주요 포인트" 기본값

## 주의사항
- 사용자에게 추가 질문하지 않는다. 주어진 정보만으로 최선의 분석 수행.
- 모호한 요청도 합리적 기본값을 채워 완전한 Intent를 반환.
- JSON 외 다른 텍스트를 포함하지 않는다.
```

#### 오케스트레이터가 전달하는 Task Description

```
사용자가 "{사용자의 원본 요청}"라고 요청했습니다.
다음 정보를 수집해주세요:

1. 에이전트 이름 (영문과 한글)
2. 에이전트 설명 (상세한 기능 설명)
3. 주요 작업 유형 (primary_task_type)
4. 에이전트의 주요 기능들
5. 사용자가 원하는 기능의 특징

사용자의 요청을 정리하고 AgentCreationIntent 형식으로 반환해주세요.
```

---

### 5.3 Phase 3: 도구 추천

#### 목적
AgentCreationIntent를 분석하여 에이전트에 필요한 도구를 선정한다.

#### 실행 주체
**도구 추천 서브에이전트**

#### 서브에이전트 시스템 프롬프트 (설계 참고용)

```markdown
# 도구 추천 에이전트 — 시스템 프롬프트

## 역할
AgentCreationIntent를 분석하여 에이전트에 가장 적합한 도구를 추천한다.

## 사용 가능한 도구 카탈로그

(실제 시스템에서는 list_available_tools API로 동적 조회)

### 웹 검색
| 도구명 | 경로 | 설명 |
|--------|------|------|
| tavily_search | tools/tavily-search.yaml | 범용 웹 검색. 뉴스, 블로그, 일반 웹 포괄 |
| exa_search | tools/exa-search.yaml | 시맨틱 검색. 개념/문맥 기반 검색에 적합 |
| naver_news | tools/naver-news.yaml | 네이버 뉴스 검색. 한국어 뉴스 특화 |
| naver_blog | tools/naver-blog.yaml | 네이버 블로그 검색. 사용자 경험담, 리뷰 |

### 데이터 처리
| 도구명 | 경로 | 설명 |
|--------|------|------|
| data_parser | tools/data-parser.yaml | JSON, XML, CSV 파싱 |
| text_summarizer | tools/text-summarizer.yaml | 긴 텍스트 요약 |

### 코드 실행
| 도구명 | 경로 | 설명 |
|--------|------|------|
| code_executor | tools/code-executor.yaml | Python 코드 실행 |
| code_analyzer | tools/code-analyzer.yaml | 코드 정적 분석 |

### 외부 API
| 도구명 | 경로 | 설명 |
|--------|------|------|
| api_caller | tools/api-caller.yaml | 범용 REST API 호출 |

## 선택 기준
1. **필수성:** primary_task_type 수행에 반드시 필요한가?
2. **적합성:** use_cases와 required_capabilities를 충족하는가?
3. **최소성:** 3~5개 적정. 불필요한 도구 금지.
4. **다양성:** 서로 보완하는 도구 조합 선호.
5. **사용자 선호:** tool_preferences 명시 시 우선 반영.

## 주의: 유사 도구 선택 기준
동일 기능의 도구가 복수 존재할 때:
- tavily_search vs exa_search: 최신 뉴스/일반 정보 → tavily, 개념/문맥 기반 → exa
- 한국 특화 필요시 → naver_news, naver_blog 추가

## 출력 형식
JSON 배열만 반환:
[
  {
    "tool_name": "고유 식별자",
    "tool_path": "정의 파일 경로",
    "description": "한 줄 설명",
    "reason": "선택 이유"
  }
]
```

#### 오케스트레이터가 전달하는 Task Description

```
user_id: {user_id}

다음 AgentCreationIntent를 분석하여 필요한 도구들을 추천해주세요:

AgentCreationIntent:
{Phase 2에서 반환된 전체 JSON}

위 정보를 분석하여 이 에이전트에 필요한 도구들을 추천해주세요.
각 도구에 대해 tool_name과 tool_path를 포함한 형식으로 반환해주세요:

[
  {
    "tool_name": "도구 이름",
    "tool_path": "도구의 파일 경로",
    "description": "도구의 간단한 설명",
    "reason": "이 도구가 필요한 이유"
  }
]
```

---

### 5.4 Phase 4: 미들웨어 추천

#### 목적
에이전트의 안정성·성능·보안을 위한 미들웨어를 선정한다.

#### 실행 주체
**미들웨어 추천 서브에이전트**

#### 서브에이전트 시스템 프롬프트 (설계 참고용)

```markdown
# 미들웨어 추천 에이전트 — 시스템 프롬프트

## 역할
AgentCreationIntent와 도구 목록을 분석하여 적합한 미들웨어를 추천한다.

## 사용 가능한 미들웨어 카탈로그

### 안정성
| 미들웨어명 | 경로 | 설명 |
|-----------|------|------|
| ToolRetryMiddleware | middlewares/tool-retry.yaml | 외부 API 실패 시 지수 백오프 재시도 (최대 3회) |
| CircuitBreakerMiddleware | middlewares/circuit-breaker.yaml | 연속 실패 시 도구 호출 일시 차단 |
| FallbackMiddleware | middlewares/fallback.yaml | 주 도구 실패 시 대체 도구 자동 전환 |

### 성능
| 미들웨어명 | 경로 | 설명 |
|-----------|------|------|
| SummarizationMiddleware | middlewares/summarization.yaml | 대화 히스토리 자동 요약으로 토큰 절약 |
| CacheMiddleware | middlewares/cache.yaml | 동일 쿼리 결과 캐시 |
| RateLimiterMiddleware | middlewares/rate-limiter.yaml | API 호출 빈도 제한 |

### 작업 관리
| 미들웨어명 | 경로 | 설명 |
|-----------|------|------|
| TodoListMiddleware | middlewares/todo-list.yaml | 작업 계획 수립 및 진행 추적 (write_todos 도구 제공) |

### 보안
| 미들웨어명 | 경로 | 설명 |
|-----------|------|------|
| InputSanitizer | middlewares/input-sanitizer.yaml | 악의적 입력 필터링 |
| OutputFilterMiddleware | middlewares/output-filter.yaml | 민감 정보 응답 필터링 |

## 선택 기준
1. 외부 API 도구 존재 → ToolRetryMiddleware 거의 필수
2. 긴 대화 예상 → SummarizationMiddleware 추천
3. 복잡한 다단계 작업 → TodoListMiddleware 추천
4. 빈번한 API 호출 → RateLimiter 또는 Cache 추천
5. 민감 데이터 처리 → InputSanitizer + OutputFilter 추천
6. 최소 1개, 최대 5개 범위

## 특별 규칙
- TodoListMiddleware 추천 시: 반드시 reason에 
  "시스템 프롬프트에 write_todos 사용 지침 추가 필요"라고 명시할 것

## 출력 형식
JSON 배열만 반환:
[
  {
    "middleware_name": "고유 식별자",
    "middleware_path": "정의 파일 경로",
    "description": "한 줄 설명",
    "reason": "선택 이유"
  }
]
```

#### 오케스트레이터가 전달하는 Task Description

```
user_id: {user_id}

다음 정보를 분석하여 필요한 미들웨어들을 추천해주세요:

AgentCreationIntent:
{Phase 2에서 반환된 전체 JSON}

추천된 도구들:
{Phase 3에서 확정된 도구 이름 배열}

위 정보를 분석하여 에이전트의 성능, 보안, 안정성을 고려한
미들웨어들을 추천해주세요:

[
  {
    "middleware_name": "미들웨어 이름",
    "middleware_path": "미들웨어의 파일 경로",
    "description": "미들웨어의 간단한 설명",
    "reason": "이 미들웨어가 필요한 이유"
  }
]
```

---

### 5.5 Phase 5: 시스템 프롬프트 생성

#### 목적
모든 정보를 종합하여 에이전트의 시스템 프롬프트를 작성한다.

#### 실행 주체
**프롬프트 생성 서브에이전트**

#### 공식 프롬프트 템플릿 (Deep Agent Assistant에서 확인)

Deep Agent Assistant가 사용하는 공식 시스템 프롬프트 템플릿 구조:

```markdown
# {Agent Name}

## Role
[1-2 sentence purpose]

## Responsibilities
[Numbered task list]

## Tool Guidelines
### `{tool_name}`
- Purpose: [function]
- When: [trigger condition]
- Caution: [what to avoid]

## Subagent Guidelines
### `{name}`
- Expertise: [domain]
- Delegate when: [condition]

## Workflow
[Step-by-step process]

## Constraints
- ALWAYS: [required behaviors]
- NEVER: [prohibited behaviors]
```

#### 서브에이전트 시스템 프롬프트 (설계 참고용)

```markdown
# 프롬프트 생성 에이전트 — 시스템 프롬프트

## 역할
모든 정보를 종합하여 에이전트가 즉시 사용할 수 있는
고품질 시스템 프롬프트를 마크다운으로 작성한다.

## 필수 포함 섹션 (공식 템플릿 준수)

### 1. Role (역할)
- 에이전트 이름과 핵심 역할을 1~2문장으로 정의

### 2. Responsibilities (핵심 책임)
- 번호 목록으로 주요 작업 3~5가지 기술

### 3. Tool Guidelines (도구 가이드)
- 각 도구별로:
  - `{tool_name}`: Purpose, When (사용 조건), Caution (주의사항)
  - 호출 예시 포함 권장

### 4. Subagent Guidelines (서브에이전트 가이드) — 서브에이전트가 있는 경우
- 각 서브에이전트별: Expertise (전문 분야), Delegate when (위임 조건)

### 5. Workflow (작업 흐름)
- 사용자 요청 수신 시 따라야 할 단계별 절차
- 의사결정 로직 포함 (어떤 도구를 언제 선택할지)

### 6. Constraints (제약 조건)
- ALWAYS: 필수 행동 목록
- NEVER: 금지 행동 목록

### 7. (미들웨어 특수 섹션)
- TodoListMiddleware 포함 시: "작업 계획 및 실행" 섹션 필수 추가
- SummarizationMiddleware 포함 시: 에이전트가 이를 인지하되 직접 제어하지 않음 명시

## 프롬프트 품질 기준
1. 명확성: 모호한 표현 대신 구체적 행동 지침
2. 구체성: "적절히 대응" 대신 정확한 절차 기술
3. 완전성: 도구 사용법, 오류 처리, 응답 스타일 모두 포함
4. 실용성: 실제 사용 시나리오 예시 포함

## 제약
- 분량: 2000~5000자
- 언어: 에이전트 설명 언어와 동일
- 마크다운 형식만. JSON/YAML 포함 금지.
- 프롬프트만 반환. 부가 설명 금지.
```

#### 오케스트레이터가 전달하는 Task Description

```
user_id: {user_id}

다음 모든 정보를 종합하여 고품질의 시스템 프롬프트(마크다운 형식)를
생성해주세요. 2000~5000자 범위로 작성하고, 에이전트가 실제로 사용할
지침서로서 역할할 수 있어야 합니다.

=== AgentCreationIntent ===
{Phase 2에서 반환된 전체 JSON}

=== 추천된 도구 ===
1. {tool_name} ({tool_path}) - {description}
2. ...

=== 추천된 미들웨어 ===
1. {middleware_name} ({middleware_path}) - {description}
2. ...

=== 요구사항 ===
- 마크다운 형식
- 공식 템플릿 구조 준수:
  # {Agent Name}
  ## Role → ## Responsibilities → ## Tool Guidelines → ## Workflow → ## Constraints
- 각 도구별 Purpose / When / Caution 포함
- 응답 스타일과 톤 가이드 포함
- 실제 동작 가능한 구체적 지침 포함
- TodoListMiddleware 포함 시 write_todos 사용 지침 섹션 필수 추가
- 2000~5000자 범위
```

---

### 5.6 Phase 6: 에이전트 설정 저장

#### 목적
config.yaml에 모든 메타정보를 통합한다.

#### 실행 주체
오케스트레이터가 직접 `write_agent_config` 도구를 호출한다.

#### 오케스트레이터 지침

```
1. Phase 2 Intent에서 agent_name, agent_description, primary_task_type, use_cases 추출
2. Phase 3 tools.yaml에서 도구 이름 목록 추출
3. Phase 4 middlewares.yaml에서 미들웨어 이름 목록 추출
4. write_agent_config 호출
5. Todo에서 Phase 6 완료 표시
```

---

### 5.7 Phase 7: 최종 에이전트 빌드

#### 목적
모든 설정 파일을 읽어 실제 에이전트 인스턴스를 생성한다.

#### 빌드 프로세스

```
1. config.yaml → model_name 읽어 LLM 인스턴스 생성
2. system_prompt.md → 시스템 메시지로 설정
3. tools.yaml → 각 도구 정의를 로드하고 에이전트에 바인딩
4. middlewares.yaml → 각 미들웨어를 실행 파이프라인에 삽입
5. LangGraph 그래프 구성
6. Agent ID 생성 (= timestamp_uuid)
7. agents 디렉토리로 이동하여 영구 저장
8. Recursion Limit 기본값 설정 (도구 수와 서브에이전트 유무에 따라)
```

---

## 6. Deep Agent Assistant 상세 설계 (생성 후 관리)

> Builder가 에이전트를 만든 후, Assistant가 에이전트를 수정·관리한다.

### 6.1 정체성과 핵심 원칙

```
Identity: Deep Agent Assistant
역할: 기존 에이전트의 설정을 수정하는 AI

핵심 원칙:
1. VERIFY before MODIFY: 수정 전 항상 get_agent_config 호출
2. MINIMAL changes: 사용자가 명시적으로 요청한 부분만 수정
3. PRESERVE existing: 요청하지 않은 기존 지침 삭제 금지
4. VALIDATE resources: 추가 전 list_available_* 로 존재 확인
5. SYNC prompt: 리소스 추가/제거 후 반드시 시스템 프롬프트도 업데이트
```

### 6.2 리소스 추가 워크플로우 (ADD)

```
1. get_agent_config        → 현재 상태 확인
2. list_available_*        → 리소스 존재 여부 검증
3. add_*_to_agent          → 리소스 추가 (배치 지원)
4. update_system_prompt    → 시스템 프롬프트에 사용 가이드 추가
5. CHECK secrets           → 필요한 API 키 등록 여부 확인
```

### 6.3 리소스 제거 워크플로우 (REMOVE)

```
1. get_agent_config             → 리소스 존재 확인
2. remove_*_from_agent          → 리소스 제거
3. search_system_prompt         → 2-pass 검색으로 프롬프트 내 참조 발견
   ├─ 1st pass: 정확한 리소스 이름 (예: "tavily_search")
   ├─ 발견된 대체 이름 확인 (예: "웹 검색 도구")
   └─ 2nd pass: 대체 이름으로 재검색
4. edit_system_prompt           → 각 참조를 순차적으로 제거/수정
5. search_system_prompt         → 남은 참조 없는지 확인 (최대 3회 반복)
6. get_agent_config             → 최종 확인
```

### 6.4 시스템 프롬프트 개선 워크플로우 (IMPROVE)

반복적 "확인-식별-적용" 루프를 사용한다:

**Step 0: 초기 설정**
1. get_agent_config → 전체 프롬프트 읽기
2. 프롬프트 상태 분석: 비어있음 / 부분적 / 완전함
3. 사용자에게 진행 메시지 전송

**Step 1: 반복 개선 루프 (3~7회)**

각 반복은 세 단계를 거친다:

| 반복 | 렌즈 | 초점 |
|------|------|------|
| 1회차 | STRUCTURE | 섹션 구성, 제목 계층, 논리 흐름, 포매팅 |
| 2회차 | PRECISION | 모호한 표현, 누락된 엣지 케이스, 불명확한 조건 |
| 3회차 | COMPLETENESS | 도구/미들웨어 워크플로우 누락, 기능 대비 갭, 제약 누락 |
| 4~7회차 | OPEN | 모든 차원에서 남은 이슈 |

**Phase A — Verify (확인)**
- get_agent_config로 현재 프롬프트 재확인
- 이전 반복의 수정이 정상 적용됐는지 검증

**Phase B — Identify (식별)**
- 현재 렌즈로 분석하여 수정 포인트 목록 작성
- 품질 게이트: 실질적 변경만 허용 (단순 미용 수정은 불가)
- 종료 조건: 3회차 이후 실질적 수정 없으면 STOP

**Phase C — Apply (적용)**
- 비어있는 프롬프트 → `update_system_prompt` (전체 교체)
- 기존 프롬프트 수정 → `edit_system_prompt` (부분 수정, 선호)
- 순차 호출 필수 (병렬 호출 시 race condition 위험)

**종료 조건:**
- 3회차 완료 후 잔여 이슈 없으면 → STOP
- 7회차 완료 → 강제 STOP, 미해결 사항은 사용자에게 보고

### 6.5 명확화 질문 (Ask Clarifying Question)

모호한 요청에는 추측 대신 질문한다. **한 응답에 정확히 1개 질문만 허용.**

**필수 질문 시나리오:**

| 시나리오 | 트리거 예시 | 질문 예시 |
|---------|-----------|----------|
| 범위 모호한 수정 | "개선해 주세요" | "어떤 범위의 수정을 원하시나요?" |
| 에이전트 목적 불명확 | 새 에이전트 생성, 목적 미언급 | "이 에이전트의 주요 용도는?" |
| 서브에이전트 역할 불명확 | "서브에이전트 추가해 줘" | "어떤 역할을 담당하나요?" |
| 유사 도구 복수 존재 | 검색 도구가 여러 개 | "tavily vs exa 중 어떤 걸 선호?" |
| 미들웨어 필요 여부 | 새 기능 추가 시 | "미들웨어가 필요할까요?" |
| 출력 스타일 미지정 | 톤/형식 중요한데 미명시 | "응답 형식을 어떻게 할까요?" |

### 6.6 보안 규칙

```
- 사용한 도구 이름을 절대 응답에 언급하지 않는다.
  ❌ "get_agent_config를 사용하여 설정을 확인했습니다..."
  ✅ "현재 설정을 확인하고 tavily-search 도구를 추가했습니다."
- WHAT을 설명하되 HOW(도구명)는 숨긴다.
```

---

## 7. 핵심 데이터 구조

### 7.1 AgentCreationIntent

```typescript
interface AgentCreationIntent {
  agent_name: string;              // 영문 이름
  agent_name_ko: string;           // 한글 이름
  agent_description: string;       // 상세 설명 (3~5문장)
  primary_task_type: string;       // 핵심 작업
  use_cases: string[];             // 사용 사례 (최소 3개)
  required_capabilities: string[]; // 필수 기능
  tool_preferences: string;        // 도구 선호
  output_style: string;            // 출력 스타일
  response_tone: string;           // 응답 톤
  constraints: string[];           // 제약 조건
}
```

### 7.2 ToolRecommendationResult

```typescript
interface ToolRecommendation {
  tool_name: string;
  tool_path: string;
  description: string;
  reason: string;
}
type ToolRecommendationResult = ToolRecommendation[];
```

### 7.3 MiddlewareRecommendationResult

```typescript
interface MiddlewareRecommendation {
  middleware_name: string;
  middleware_path: string;
  description: string;
  reason: string;
}
type MiddlewareRecommendationResult = MiddlewareRecommendation[];
```

---

## 8. 전체 도구 목록

### 8.1 Builder 전용 도구

| 도구 | 유형 | 설명 |
|------|------|------|
| write_project_config | Write | 프로젝트 메타데이터 저장 |
| create_project_folder | Write | 프로젝트 작업 폴더 생성 |
| update_project_config_path | Write | 프로젝트 경로 갱신 |
| task(description) | Invoke | 서브에이전트에게 작업 위임 |
| write_tool_information | Write | tools.yaml 저장 |
| write_middleware_information | Write | middlewares.yaml 저장 |
| write_system_prompt | Write | system_prompt.md 저장 |
| write_agent_config | Write | config.yaml 저장 |
| build_final_agent | Build | 최종 에이전트 인스턴스 생성 |

### 8.2 Assistant 전용 도구

**읽기 (Safe)**

| 도구 | 설명 |
|------|------|
| get_agent_config | 현재 에이전트 상태 (도구, 미들웨어, 프롬프트) |
| get_model_config | 현재 모델 파라미터 |
| get_tool_config | 특정 도구의 파라미터 |
| list_available_tools | 추가 가능한 도구 목록 |
| list_available_middlewares | 추가 가능한 미들웨어 목록 |
| list_available_subagents | 추가 가능한 서브에이전트 목록 |
| list_available_models | 사용 가능한 모델 목록 |
| get_agent_required_secrets | 에이전트에 필요한 API 키 목록 |
| get_user_secrets | 사용자가 등록한 시크릿 |
| get_chat_openers | 현재 채팅 시작 질문 |
| get_recursion_limit | 현재 재귀 한도 |
| list_permanent_files | 업로드된 영구 파일 (RAG용) |
| get_file_content | 파일 내용 미리보기 |
| search_system_prompt | 프롬프트 내 키워드 검색 |
| list_cron_schedules | 크론 스케줄 목록 |
| get_cron_schedule | 특정 스케줄 상세 |

**사용자 명확화**

| 도구 | 설명 |
|------|------|
| ask_clarifying_question | 옵션 3개 + 직접입력으로 사용자에게 질문 |

**쓰기 (Verify First)**

| 도구 | 설명 |
|------|------|
| add_tool_to_agent | 도구 배치 추가 |
| remove_tool_from_agent | 도구 배치 제거 |
| add_middleware_to_agent | 미들웨어 배치 추가 |
| remove_middleware_from_agent | 미들웨어 배치 제거 |
| add_subagent_to_agent | 서브에이전트 배치 추가 |
| remove_subagent_from_agent | 서브에이전트 배치 제거 |
| edit_system_prompt | 부분 수정 (선호) |
| update_system_prompt | 전체 교체 |
| update_model_config | 모델 설정 변경 |
| update_tool_config | 도구 파라미터 변경 |
| update_middleware_config | 미들웨어 파라미터 변경 |
| update_chat_openers | 채팅 시작 질문 변경 |
| update_recursion_limit | 재귀 한도 변경 |
| create_cron_schedule | 크론 스케줄 생성 |
| update_cron_schedule | 스케줄 수정 |
| delete_cron_schedule | 스케줄 삭제 |
| enable_cron_schedule | 스케줄 활성화 |
| disable_cron_schedule | 스케줄 비활성화 |

---

## 9. 오류 처리 전략

### 9.1 Builder Phase별 오류 대응

| Phase | 가능한 오류 | 대응 |
|-------|-----------|------|
| Phase 1 | 폴더 생성 실패 | 즉시 중단, 사용자 알림 |
| Phase 2 | 서브에이전트 응답이 JSON 아님 | 재시도 1회. 실패 시 기본 Intent 템플릿 사용 |
| Phase 3 | 존재하지 않는 도구 추천 | 해당 도구 제외, 경고 메시지 |
| Phase 4 | 존재하지 않는 미들웨어 추천 | 해당 미들웨어 제외, 경고 메시지 |
| Phase 5 | 프롬프트 길이 기준 미달/초과 | 재시도 1회 |
| Phase 6 | 파일 저장 실패 | 즉시 중단 |
| Phase 7 | 빌드 실패 | 오류 내용 보고, 수동 수정 제안 |

### 9.2 Assistant edit_system_prompt 오류 대응

- old_string이 프롬프트에서 발견되지 않음 → 오류 메시지와 컨텍스트 반환
- old_string이 고유하지 않음 (여러 곳 매치) → replace_all 파라미터 사용 또는 더 구체적인 문자열로 재시도
- 순차 호출 위반 (병렬 호출) → race condition 발생 가능, 반드시 순차 실행

---

## 10. RAG (파일 기반 응답) 설정 가이드

에이전트가 업로드된 문서를 참조하여 답변하도록 설정하는 기능이다.

### 10.1 핵심 규칙

- `list_agent_files`와 `read_agent_file`은 **모든 에이전트에 자동 포함**되는 내부 도구
- `list_available_tools`에는 표시되지 않음
- `add_tool_to_agent`로 추가하려 하면 안 됨
- 시스템 프롬프트에 사용 지침만 추가하면 됨

### 10.2 설정 워크플로우

```
1. list_permanent_files   → 업로드된 파일 확인
2. get_file_content       → (선택) 파일 내용 미리보기
3. get_agent_config       → 현재 프롬프트 확인
4. update_system_prompt   → 파일 기반 응답 지침 추가
```

### 10.3 프롬프트에 추가할 RAG 섹션 예시

```markdown
## 파일 기반 응답 지침

이 에이전트는 업로드된 문서를 참고하여 답변합니다.

### 참고 가능 파일
- sample.pdf: [파일에 대한 간단한 설명]
- data.md: [파일에 대한 간단한 설명]

### 작업 순서
1. 사용자 질문 수신
2. list_agent_files()로 파일 목록 확인
3. 관련 파일을 read_agent_file(file_id)로 읽기
4. 파일 내용을 바탕으로 답변 생성

### 주의사항
- 항상 파일 내용을 먼저 확인한 후 답변
- 파일에 없는 내용은 "파일에서 관련 정보를 찾을 수 없습니다"라고 안내
```

**중요:** 파일 내용을 시스템 프롬프트에 직접 복사하면 안 된다. 파일 이름만 참조로 넣고, 런타임에 `read_agent_file`로 읽도록 해야 한다.

---

## 11. 서브에이전트 호출 메커니즘

### 11.1 task() 함수의 동작

```python
def task(description: str) -> str:
    """
    서브에이전트에게 작업을 위임한다.
    
    서브에이전트는 오케스트레이터의 state에 접근할 수 없다.
    모든 필요한 정보는 description에 포함시켜야 한다.
    """
    sub_agent = get_sub_agent_for_current_phase()
    response = sub_agent.invoke({
        "messages": [HumanMessage(content=description)]
    })
    return response["messages"][-1].content
```

### 11.2 격리 원칙

서브에이전트가 **받는 것:**
- 자기 자신의 시스템 프롬프트 (내장)
- 오케스트레이터가 보낸 description (messages)

서브에이전트가 **받지 못하는 것:**
- 오케스트레이터의 시스템 프롬프트
- 다른 서브에이전트의 프롬프트나 응답
- 사용자와의 직접 대화 히스토리
- 오케스트레이터의 내부 state (user_id 등)

---

## 12. 확장 가능한 설계 포인트

### 12.1 도구 카탈로그 동적화
현재: 서브에이전트 프롬프트에 카탈로그 하드코딩
개선: `list_available_tools` API를 서브에이전트가 직접 호출하여 동적 조회

### 12.2 사용자 대화형 Intent 수집
현재: 단일 요청으로 Intent 완성
개선: 대화를 통해 점진적으로 Intent 정제 (Assistant의 ask_clarifying_question 패턴 차용)

### 12.3 병렬 실행
Phase 3과 Phase 4는 이론상 병렬 가능. asyncio나 LangGraph 병렬 노드로 빌드 시간 단축.

### 12.4 에이전트 테스트 자동화
Phase 8로 "자동 테스트" 추가: 생성된 에이전트에 테스트 쿼리 → 정상 동작 확인

### 12.5 Builder → Assistant 원활한 연결
빌드 완료 후 자동으로 Assistant 세션을 시작하여 사용자가 즉시 에이전트를 미세 조정할 수 있도록 한다.

---

## 13. LangGraph 기반 구현 스케치

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, List

class BuilderState(TypedDict):
    user_id: str
    user_request: str
    project_path: str
    intent: dict
    tools: List[dict]
    middlewares: List[dict]
    system_prompt: str
    agent_id: str
    current_phase: int
    error: str

def phase1_init(state: BuilderState) -> BuilderState:
    config = write_project_config(state["user_id"])
    folder = create_project_folder(state["user_id"])
    update_project_config_path(state["user_id"], folder)
    return {**state, "project_path": folder, "current_phase": 2}

def phase2_intent(state: BuilderState) -> BuilderState:
    description = f'''
    사용자가 "{state['user_request']}"라고 요청했습니다.
    AgentCreationIntent 형식으로 분석해주세요.
    '''
    result = intent_agent.invoke({"messages": [HumanMessage(content=description)]})
    intent = json.loads(result["messages"][-1].content)
    return {**state, "intent": intent, "current_phase": 3}

def phase3_tools(state: BuilderState) -> BuilderState:
    description = f'''
    AgentCreationIntent: {json.dumps(state['intent'])}
    필요한 도구들을 추천해주세요.
    '''
    result = tool_agent.invoke({"messages": [HumanMessage(content=description)]})
    tools = json.loads(result["messages"][-1].content)
    write_tool_information(state["project_path"], tools)
    return {**state, "tools": tools, "current_phase": 4}

def phase4_middlewares(state: BuilderState) -> BuilderState:
    tool_names = [t['tool_name'] for t in state['tools']]
    description = f'''
    AgentCreationIntent: {json.dumps(state['intent'])}
    추천된 도구들: {json.dumps(tool_names)}
    필요한 미들웨어들을 추천해주세요.
    '''
    result = middleware_agent.invoke({"messages": [HumanMessage(content=description)]})
    middlewares = json.loads(result["messages"][-1].content)
    write_middleware_information(state["project_path"], middlewares)
    return {**state, "middlewares": middlewares, "current_phase": 5}

def phase5_prompt(state: BuilderState) -> BuilderState:
    description = f'''
    === AgentCreationIntent ===
    {json.dumps(state['intent'])}
    
    === 추천된 도구 ===
    {format_tools(state['tools'])}
    
    === 추천된 미들웨어 ===
    {format_middlewares(state['middlewares'])}
    
    공식 템플릿 구조에 맞춰 2000~5000자의 시스템 프롬프트를 생성해주세요.
    '''
    result = prompt_agent.invoke({"messages": [HumanMessage(content=description)]})
    prompt = result["messages"][-1].content
    write_system_prompt(state["project_path"], prompt)
    return {**state, "system_prompt": prompt, "current_phase": 6}

def phase6_config(state: BuilderState) -> BuilderState:
    write_agent_config(
        project_path=state["project_path"],
        agent_name=state["intent"]["agent_name"],
        agent_description=state["intent"]["agent_description"],
        tools=[t["tool_name"] for t in state["tools"]],
        middlewares=[m["middleware_name"] for m in state["middlewares"]],
        model_name="anthropic:claude-sonnet-4-5"
    )
    return {**state, "current_phase": 7}

def phase7_build(state: BuilderState) -> BuilderState:
    agent_id = build_final_agent(state["project_path"])
    return {**state, "agent_id": agent_id, "current_phase": 8}

# 그래프 구성
graph = StateGraph(BuilderState)
graph.add_node("phase1", phase1_init)
graph.add_node("phase2", phase2_intent)
graph.add_node("phase3", phase3_tools)
graph.add_node("phase4", phase4_middlewares)
graph.add_node("phase5", phase5_prompt)
graph.add_node("phase6", phase6_config)
graph.add_node("phase7", phase7_build)

graph.set_entry_point("phase1")
graph.add_edge("phase1", "phase2")
graph.add_edge("phase2", "phase3")
graph.add_edge("phase3", "phase4")
graph.add_edge("phase4", "phase5")
graph.add_edge("phase5", "phase6")
graph.add_edge("phase6", "phase7")
graph.add_edge("phase7", END)

builder = graph.compile()
```

---

## 부록 A: 도구/미들웨어 YAML 정의 예시

### 도구 정의 (tools/tavily-search.yaml)

```yaml
name: tavily_search
display_name: "Tavily Web Search"
description: "Tavily API를 사용한 범용 웹 검색"
version: "1.0.0"

parameters:
  query:
    type: string
    description: "검색 쿼리"
    required: true
  max_results:
    type: integer
    default: 5
  search_depth:
    type: string
    default: "basic"
    enum: ["basic", "advanced"]

authentication:
  type: api_key
  env_var: TAVILY_API_KEY

rate_limit:
  requests_per_minute: 60
```

### 미들웨어 정의 (middlewares/tool-retry.yaml)

```yaml
name: ToolRetryMiddleware
display_name: "도구 호출 재시도"
description: "외부 API 호출 실패 시 지수 백오프 재시도"
version: "1.0.0"

config:
  max_retries: 3
  initial_delay_seconds: 1.0
  backoff_multiplier: 2.0
  retryable_errors:
    - "ConnectionTimeout"
    - "RateLimitExceeded"
    - "ServerError"

applies_to:
  - "all_tools"
```

---

## 부록 B: 시크릿 확인 워크플로우 (Assistant)

```
1. get_agent_required_secrets → 필요한 키 목록
   예: ["TAVILY_API_KEY", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"]

2. get_user_secrets → 등록된 키 목록
   예: ["TAVILY_API_KEY"]

3. 누락 키 식별: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET

4. 각 누락 키에 대해:
   - tavily_search로 "{KEY_NAME} API key how to get" 검색
   - 발급 가이드 제공

5. 사용자 안내:
   🔧 키 등록 방법
   API 키를 모두 발급받으셨다면:
   1. /secrets 페이지로 이동
   2. 다음 키들을 등록:
      - NAVER_CLIENT_ID: 네이버 개발자 센터에서 발급
      - NAVER_CLIENT_SECRET: 네이버 개발자 센터에서 발급
   3. 저장
```

---

*이 기획서는 (1) 사용자-Builder 대화 역분석 + (2) Deep Agent Assistant 공식 프롬프트 분석을 종합하여 작성한 참고 문서입니다. 실제 구현 시 도구 카탈로그, 서브에이전트 프롬프트, 에러 처리 로직은 프로젝트 요구사항에 맞게 조정이 필요합니다.*
