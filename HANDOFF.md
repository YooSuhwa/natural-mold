# 작업 인계 문서

**브랜치**: `feature/greenfield-credentials`  **상태**: 12 마일스톤 + 8 hotfix 완료, PR 머지 대기
**마지막 커밋**: `44a0514` — OpenRouter URL 라우팅 fix
**테스트**: 665 backend pytest PASS, ruff/branding clean, frontend build PASS

---

## 완료된 작업

- [x] **M0~M11** Credential/Tools/Skills/Models 그린필드 + Hook + Health + Spend + Fallback + 자동 카탈로그
- [x] 사용자 검증 후 hotfix 8건 (커밋 9c10b70~44a0514):
  - Add Model dialog credential select UUID 표시
  - SSL `truststore` 도입 (macOS Keychain)
  - GPT-5 family `max_completion_tokens` 분기
  - OpenAI `base_url` 환경변수 우회 방지
  - OpenRouter / openai-compatible base_url forwarding
  - Anthropic test recipe → `/v1/models` GET (토큰 소비 0)
  - Health Check 'Check now' credential 명시 + 토스트 정확성
  - raw_request에 GPT-5 payload 정확히 표시

## 다음에 해야 할 작업 (우선순위)

1. **PR 생성 + 머지** — `gh pr create --base main --body-file tasks/archive/HANDOFF-greenfield-m0-m11.md`
2. **사용자 환경별 추가 검증** — 다른 사용자 OpenAI/Anthropic 키로 동작 재검증
3. **테스트 환경 정리** (선택):
   - `pkill -f "uvicorn app.main:app --port 8002"` + `pkill -f "next dev"`
   - `docker stop moldy-postgres-test && docker rm moldy-postgres-test && docker volume rm moldy_postgres_test_data`
4. **본 환경 5432 DB에 m18~m22 적용** (사용자 확인 필요): `docker-compose down -v && docker-compose up -d postgres && cd backend && uv run alembic upgrade head`

## 후속 (별도 PR 권장)

- agent_mcp_servers 링크 테이블, OAuth2 PKCE, Vault AppRole/JWT, Health history retention cron
- Object Permission 중앙화 (멀티테넌트 전환 시)
- mcp_server_registry 확장 (Discord/Confluence/Asana 등)

## 주의사항

- **환경변수 충돌 주의**: 셸의 `OPENAI_BASE_URL` (RunPod proxy)이 OpenAI SDK에 누수되어 모델 Test 404 야기. M11 hotfix로 우회 처리됨.
- **GPT-5/o1/o3/o4 family**: `max_completion_tokens` + temperature 미지정 강제. `_GPT5_FAMILY_PREFIXES` 기준 분기.
- **건드리면 안 되는 파일**: `backend/app/data/litellm_model_catalog.json` (legacy fallback), `backend/alembic/versions/m12_drop_legacy_columns.py` (frozen history)
- **테스트 환경 DB**: `localhost:5433` (격리), 본 환경 `5432`는 무손상

## 핵심 파일

- 거버넌스: `PLAN.md`, `CHECKPOINT.md`, `tasks/archive/HANDOFF-greenfield-m0-m11.md` (전체 변경 요약)
- 카탈로그: `backend/app/services/model_catalog/{loaders,normalize,merge,resolve}.py`, `backend/app/data/model_catalog/`
- 모델 Test: `backend/app/services/model_test.py`, `backend/app/agent_runtime/model_factory.py`
- Health Check: `backend/app/services/health_check.py`, `backend/app/routers/health.py`
- 차용 명기: `NOTICES.md` (n8n + LiteLLM + ai-model-list)

## 마지막 상태

- 테스트 환경 backend 8002 + frontend 3000 가동 중
- catalog.json: 2634 models / 3924 provider+models / rankings 389 (LMArena/LiveBench)
- 6시간 cron 자동 갱신 중

새 세션에서 "HANDOFF.md 읽고 이어서" 하면 즉시 컨텍스트 복원 가능.
