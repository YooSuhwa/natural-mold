DEFAULT_TOOLS = [
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
]
