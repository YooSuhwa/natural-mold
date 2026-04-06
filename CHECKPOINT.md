# CHECKPOINT — LLM 모델 관리 시스템 전면 개편

## M1: Backend 인프라 + Provider API
- [x] encryption.py + LLMProvider ORM + Alembic migration 1
- [x] Provider CRUD API + 연결 테스트 + 모델 검색 서비스
- [x] 시드 데이터 (default_providers)
- 검증: `cd backend && uv run ruff check . && uv run pytest`
- done-when: 린트 통과, 테스트 통과, Provider CRUD + discover 동작
- 상태: done

## M2: Backend Models 수정 + model_factory 확장
- [x] Models API 수정 (provider_id, 메타 컬럼, bulk create)
- [x] model_factory 확장 (openrouter, openai_compatible)
- [x] 실행코드 수정 (conversations.py, trigger_executor.py, chat_service.py)
- 검증: `cd backend && uv run ruff check . && uv run pytest`
- done-when: 린트 통과, 테스트 통과, 새 provider로 LLM 호출 가능
- 상태: done

## M3: Frontend 전면 재구성
- [x] 타입 + API 클라이언트 + Hooks (Provider, Model 수정)
- [x] Models 페이지 재작성 (탭: Providers | Models)
- [x] Provider 관리 UI (카드 + 폼 + 연결 테스트)
- [x] 모델 브라우저 + 추가 Dialog (자동 검색 + 선택)
- [x] 에이전트 설정 모델 선택기 개선
- [x] i18n 키 추가
- 검증: `cd frontend && pnpm build`
- done-when: 빌드 성공, 타입 에러 0
- 상태: done

## M4: 통합 검증 + 마이그레이션 2
- [x] Backend 테스트 (providers, model_discovery, models bulk)
- [x] 전체 검증 (ruff + pytest + pnpm build)
- [ ] Alembic Migration 2 (api_key_encrypted, base_url 컬럼 DROP) — 보류: conversations.py에서 model-level key 폴백 사용 중
- 검증: `cd backend && uv run ruff check . && uv run pytest && cd ../frontend && pnpm build`
- done-when: 전체 테스트 통과, 빌드 성공
- 상태: done
