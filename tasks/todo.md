# Phase 1.5 — 스킬 빌더 챗 후속 2건 (feature/skill-builder-phase1.5)

브리핑: 메모리 skill-builder-chat-impl ▶ 다음 세션 시작점. origin/main(56c3faf3) 기준 새 브랜치.

## M-A: create 탭 최초 요청의 자동 첫 메시지화 (프론트)

- [x] A1. `lib/types/skill-builder.ts` — `SkillBuilderStatus`에 `active`/`abandoned` 추가 (백엔드 enum 정합, i18n은 이미 존재)
- [x] A2. `_components/skill-builder-auto-request.tsx` 신설 — composerHint 슬롯(AssistantRuntimeProvider 안)에서 `useAui().thread().append()` 1회 발화. ref latch + `useAuiState(s => s.thread?.isEmpty)` 이중 가드
- [x] A3. `skill-builder-chat-client.tsx` — 가드: envelope 로드됨 && messages 0 && !active_run && !latest_run && status==='active' && user_request 존재 → text 전달. create/improve 양쪽 적용
- [x] A4. vitest — 가드 헬퍼 + 컴포넌트(1회 발화 / 재발화 금지 / thread 비어있지 않으면 no-op)
- [x] A5. E2E `skill-builder-chat.spec.ts` — 진입 시 자동 발화 대기(scripted 폴백 "E2E scripted document model is ready."), 리로드 후 user_request 버블 1회 단언, conflict 테스트 auto-run 대기
- [x] A6. `captures/captures-skill-builder.spec.ts` — 03/13 진입 캡처 전 auto-run 완료 대기
- 검증: `pnpm vitest run` 전체 / tsc / eslint / build
- done-when: 빌더 진입 시 user_request가 자동 전송·응답 완료, 리로드 재전송 0건

## M-B: 바이너리 패키지 finalize (백엔드)

- [x] B1. `skills/package_builder.py` — `build_skill_zip_bytes_from_dir(slug, root, include_evals, exclude_top_dirs)` 신설 (디스크 바이트 그대로, symlink 제외, normalize_draft_path 방어, SKILL.md 필수)
- [x] B2. `services/skill_draft_workspace.py` — `build_workspace_zip_bytes(storage_path, slug)` 래퍼(inputs/ 제외), `binary_package_files` 제거, `_iter_draft_paths`/`load_draft_files` docstring 갱신
- [x] B3. `services/skill_builder_confirmation.py` — `_draft_zip_bytes`: `session.draft_workspace_path` 있으면 디스크 zip(워크스페이스=source of truth), 없으면 기존 text 경로(v1 REST 호환)
- [x] B4. `services/skill_builder_finalize.py` — BINARY_FILES_UNSUPPORTED 게이트 제거, `except PackageError` → claim 해제 + `PACKAGE_INVALID` 반환(self-heal)
- [x] B5. 테스트 — package_builder 유닛(바이너리 포함/evals·inputs 제외/symlink skip), finalize create 바이너리 성공(저장 트리 바이트 동일), improve 시드 바이너리 유지, PACKAGE_INVALID+REVIEW 복귀
- [x] B6. 스펙 문서·CHECKPOINT.md Phase 1.5 반영
- 검증: `uv run --with pytest-xdist pytest -q -n 8` / ruff
- done-when: 바이너리 asset 있는 드래프트가 finalize 성공 + 저장 스킬에 바이트 보존, 검증은 기존 text 어댑터 유지

## 최종 검증
- [x] backend 전체 pytest + ruff
- [x] frontend vitest 전체 + tsc + eslint + build
- [x] E2E skill-builder-chat.spec (throwaway 스택: fresh 포트, E2E_LLM_* 비움, DB 재생성)
- [x] 커밋 분리: M-B(backend) → M-A(frontend) → 테스트/E2E
