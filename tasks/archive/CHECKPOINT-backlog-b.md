# CHECKPOINT — 커스텀 도구 credential 통합 (백로그 B)

**브랜치**: `feature/custom-tool-credentials`
**플랜**: `~/.claude/plans/mcp-idempotent-avalanche.md`
**시작**: 2026-04-17

## M1: Backend — CUSTOM 타입 PATCH 허용 + 테스트

- [x] services/tool_service.py:266-269 — `update_tool_auth_config`에 CUSTOM 추가, owner 체크 확장
- [x] tests/test_tools.py — `test_update_custom_tool_credential` + `test_update_custom_tool_unset_credential` 2건 추가
- [x] tests/test_tools_router_extended.py — 의미가 반전된 `test_update_auth_config_non_prebuilt_returns_404`를 IDOR(다른 사용자 CUSTOM → 404) 검증으로 갱신
- 검증: `cd backend && uv run ruff check . && uv run pytest`
- done-when: 신규 2건 통과, 회귀 0
- 상태: done (ruff PASS, 539 passed)
- 담당: 젠슨

## M2: Frontend — 커스텀 도구 credential UI

- [x] components/tool/custom-auth-dialog.tsx (신규) — PrebuiltAuthDialog 패턴, provider 필터 없음
- [x] components/tool/add-tool-dialog.tsx — 커스텀 탭 inline auth 제거 + CredentialSelect 통합
- [x] app/tools/page.tsx — ToolCard isCustom 분기에 "인증 설정" 버튼 + 상태 배지
- [x] messages/ko.json — `tool.customAuth.*` 신규
- [x] tests/components/tool/add-tool-dialog.test.tsx — 신규 credential UI에 맞게 갱신
- [x] lib/types/index.ts — ToolCustomCreateRequest에 credential_id 추가
- 검증: `cd frontend && pnpm lint && pnpm build` PASS, `pnpm test add-tool-dialog` 11/11 PASS
- done-when: build PASS, lint 0 errors
- 상태: done
- 담당: 저커버그

## M3: 통합 검증 + HANDOFF

- [x] 백엔드 회귀: `cd backend && uv run pytest` — 539 passed
- [x] 프론트 풀빌드: `cd frontend && pnpm build` — 14 routes, lint 0 errors
- [x] HANDOFF.md 업데이트 (백로그 B 완료, 다음은 백로그 C)
- 검증: backend ruff PASS, pytest 539 passed; frontend lint PASS, build PASS
- done-when: 회귀 0, HANDOFF 갱신
- 상태: done
- 담당: 베조스
