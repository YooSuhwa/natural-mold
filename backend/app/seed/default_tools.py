DEFAULT_TOOLS = [
    # -----------------------------------------------------------------------
    # Core tools (no API key required)
    # -----------------------------------------------------------------------
    {
        "name": "Web Search",
        "type": "builtin",
        "is_system": True,
        "description": "웹에서 키워드를 검색하여 관련 정보를 찾습니다. 뉴스, 기사, 정보 검색에 사용하세요.",
        "parameters_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "검색 키워드"}},
            "required": ["query"],
        },
    },
    {
        "name": "Web Scraper",
        "type": "builtin",
        "is_system": True,
        "description": "웹 페이지의 텍스트 내용을 가져옵니다. URL을 입력하면 해당 페이지의 주요 텍스트를 추출합니다.",
        "parameters_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "스크래핑할 URL"}},
            "required": ["url"],
        },
    },
    {
        "name": "Current DateTime",
        "type": "builtin",
        "is_system": True,
        "description": "현재 날짜와 시간을 반환합니다. 오늘 날짜, 현재 시간, 요일을 알려줍니다.",
        "parameters_schema": {
            "type": "object",
            "properties": {},
        },
    },
    # -----------------------------------------------------------------------
    # Naver Search API tools (requires NAVER_CLIENT_ID / NAVER_CLIENT_SECRET)
    # -----------------------------------------------------------------------
    {
        "name": "Naver Blog Search",
        "type": "prebuilt",
        "is_system": True,
        "description": "네이버 블로그에서 키워드를 검색합니다. 블로그 포스트, 리뷰, 개인 의견 등을 찾을 때 사용하세요.",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 키워드"},
                "display": {"type": "integer", "description": "결과 수 (1-100, 기본 10)", "default": 10},
                "start": {"type": "integer", "description": "시작 위치 (1-1000, 기본 1)", "default": 1},
                "sort": {"type": "string", "enum": ["sim", "date"], "description": "정렬: sim(정확도순), date(날짜순)", "default": "sim"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "Naver News Search",
        "type": "prebuilt",
        "is_system": True,
        "description": "네이버 뉴스에서 키워드를 검색합니다. 최신 뉴스, 기사, 보도 내용을 찾을 때 사용하세요.",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 키워드"},
                "display": {"type": "integer", "description": "결과 수 (1-100, 기본 10)", "default": 10},
                "start": {"type": "integer", "description": "시작 위치 (1-1000, 기본 1)", "default": 1},
                "sort": {"type": "string", "enum": ["sim", "date"], "description": "정렬: sim(정확도순), date(날짜순)", "default": "sim"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "Naver Image Search",
        "type": "prebuilt",
        "is_system": True,
        "description": "네이버에서 이미지를 검색합니다. 사진, 일러스트, 인포그래픽 등을 찾을 때 사용하세요.",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 키워드"},
                "display": {"type": "integer", "description": "결과 수 (1-100, 기본 10)", "default": 10},
                "start": {"type": "integer", "description": "시작 위치 (1-1000, 기본 1)", "default": 1},
                "sort": {"type": "string", "enum": ["sim", "date"], "description": "정렬: sim(정확도순), date(날짜순)", "default": "sim"},
                "filter": {"type": "string", "enum": ["all", "large", "medium", "small"], "description": "이미지 크기 필터", "default": "all"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "Naver Shopping Search",
        "type": "prebuilt",
        "is_system": True,
        "description": "네이버 쇼핑에서 상품을 검색합니다. 가격 비교, 상품 정보 조회에 사용하세요.",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 키워드"},
                "display": {"type": "integer", "description": "결과 수 (1-100, 기본 10)", "default": 10},
                "start": {"type": "integer", "description": "시작 위치 (1-1000, 기본 1)", "default": 1},
                "sort": {"type": "string", "enum": ["sim", "asc", "dsc"], "description": "정렬: sim(정확도순), asc(가격낮은순), dsc(가격높은순)", "default": "sim"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "Naver Local Search",
        "type": "prebuilt",
        "is_system": True,
        "description": "네이버에서 지역 업체를 검색합니다. 맛집, 카페, 병원 등 주변 업체를 찾을 때 사용하세요.",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 키워드 (예: '강남역 맛집')"},
                "display": {"type": "integer", "description": "결과 수 (1-5, 기본 5)", "default": 5},
                "start": {"type": "integer", "description": "시작 위치 (기본 1)", "default": 1},
                "sort": {"type": "string", "enum": ["random", "comment"], "description": "정렬: random(기본), comment(리뷰순)", "default": "random"},
            },
            "required": ["query"],
        },
    },
    # -----------------------------------------------------------------------
    # Google Custom Search API tools (requires GOOGLE_API_KEY / GOOGLE_CSE_ID)
    # -----------------------------------------------------------------------
    {
        "name": "Google Search",
        "type": "prebuilt",
        "is_system": True,
        "description": "구글에서 웹 페이지를 검색합니다. 영문 검색, 글로벌 정보 검색에 특히 유용합니다.",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 키워드"},
                "num": {"type": "integer", "description": "결과 수 (1-10, 기본 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "Google News Search",
        "type": "prebuilt",
        "is_system": True,
        "description": "구글 뉴스에서 키워드를 검색합니다. 글로벌 뉴스, 영문 기사를 찾을 때 사용하세요.",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 키워드"},
                "num": {"type": "integer", "description": "결과 수 (1-10, 기본 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "Google Image Search",
        "type": "prebuilt",
        "is_system": True,
        "description": "구글에서 이미지를 검색합니다. 글로벌 이미지, 영문 키워드 검색에 유용합니다.",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 키워드"},
                "num": {"type": "integer", "description": "결과 수 (1-10, 기본 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
    # -----------------------------------------------------------------------
    # Google Workspace tools
    # -----------------------------------------------------------------------
    {
        "name": "Google Chat Send",
        "type": "prebuilt",
        "is_system": True,
        "description": "Google Chat 채널에 메시지를 전송합니다. 알림, 보고, 요약 결과 공유 등에 사용하세요.",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "전송할 메시지 텍스트"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "Gmail Read",
        "type": "prebuilt",
        "is_system": True,
        "description": "Gmail에서 이메일을 검색하고 읽습니다. 검색 쿼리로 필터링할 수 있습니다.",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail 검색 쿼리 (예: 'is:unread', 'from:boss@company.com')",
                    "default": "is:inbox",
                },
                "max_results": {
                    "type": "integer",
                    "description": "가져올 이메일 수 (1-20, 기본 5)",
                    "default": 5,
                },
            },
        },
    },
    {
        "name": "Gmail Send",
        "type": "prebuilt",
        "is_system": True,
        "description": "Gmail로 이메일을 전송합니다. 수신자, 제목, 본문을 지정하여 이메일을 보냅니다.",
        "parameters_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "수신자 이메일 주소"},
                "subject": {"type": "string", "description": "이메일 제목"},
                "body": {"type": "string", "description": "이메일 본문 (텍스트)"},
            },
            "required": ["to", "subject", "body"],
        },
    },
]
