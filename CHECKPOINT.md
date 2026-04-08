# CHECKPOINT — Agent Image Generation (Moldy Character)

## M1: Backend Schema + Config
- [x] Agent 모델에 image_path 컬럼 추가 + Alembic 마이그레이션
- [x] config.py에 이미지 생성 설정 추가 (image_gen_api_key 등)
- [x] AgentResponse 스키마에 image_url 필드 추가
- 검증: `cd backend && uv run ruff check . && uv run pytest tests/test_agents.py -x`
- done-when: ruff 에러 0, 기존 에이전트 테스트 통과
- 상태: done

## M2: Backend Image Service + Router
- [x] image_service.py 신규 생성 (OpenRouter + Gemini Flash Image 호출)
- [x] POST /api/agents/{id}/image, GET /api/agents/{id}/image 엔드포인트
- [x] _agent_to_response에 image_url 필드 추가
- [x] moldy_main.png 참조 이미지 배치
- 검증: `cd backend && uv run ruff check . && uv run pytest tests/test_agents.py -x`
- done-when: ruff 에러 0, 기존 테스트 통과
- 상태: done

## M3: Frontend Types + Components
- [x] Agent 타입에 image_url 추가
- [x] AgentAvatar 공용 컴포넌트 생성
- [x] generateImage API + useGenerateAgentImage 훅
- [x] BotIcon → AgentAvatar 교체 (6곳)
- [x] 설정 페이지에 이미지 생성 UI
- [x] i18n 메시지 추가
- 검증: `cd frontend && pnpm build && pnpm lint`
- done-when: 빌드 성공, 린트 통과
- 상태: done

## M4: Final Verification
- [x] 백엔드 전체 테스트 통과
- [x] 프론트엔드 빌드 + 린트 통과
- [x] 기존 테스트 깨짐 없음
- 검증: `cd backend && uv run ruff check . && uv run pytest tests/test_agents.py -x && cd ../frontend && pnpm build`
- done-when: 모든 검증 통과
- 상태: done
