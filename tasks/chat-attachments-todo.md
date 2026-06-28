# 채팅 첨부 파일 표시 — Phase 0 + P1 (todo)

Source of truth: `docs/design-docs/chat-attachments-dev-plan.md`
Plan: `~/.claude/plans/frolicking-marinating-quilt.md`
범위: Phase 0(보안) + P1(표시). **P2(모델 multimodal)는 제외.** 커밋 7개 분리.

## 커밋 1 — fix(security): authn+ownership guard on GET /api/uploads/{id} (+ orphan GC) ✅ 615d661b
- [x] `get_upload`에 `Depends(get_current_user)` + 소유권(`row.user_id == user.id`), 미존재/권한없음 모두 404
- [x] orphan GC 잡(message_id IS NULL AND created_at < now()-24h → 행+디스크 삭제) 스케줄러 등록
- [x] pytest: 비인증 401/타유저 404, 본인 200, GC 단위
- [x] `uv run pytest` + `uv run ruff check .` green → commit

## 커밋 2 — feat(chat): backfill message_attachments.message_id on turn finalize (M1) ✅ 224d73cf
- [x] 검증 테스트: resolver id == list_messages_from_checkpointer user id (동일 트리 주입)
- [x] `resolve_turn_user_message_id(db, conversation, *, tree)` (트리 마지막 human → parse_msg_id)
- [x] `link_attachments_to_message(db, *, attachment_ids, message_id)` (WHERE id IN ids AND message_id IS NULL)
- [x] threading: start_conversation_run → _run_conversation → finalize 직후 _backfill_turn_attachments
- [x] pytest: echo on 올바른 버블, idless idx fallback, 멀티턴, 빈 무동작, worker wiring
- [x] green → commit

## 커밋 3 — feat(chat): gate attachment hydration to authed views (M2) ✅ 0a891b41
- [x] 첨부 hydration을 `if user_id is not None:`로 감쌈
- [x] pytest: 인증 echo / 공유(user_id=None) 미노출
- [x] green → commit

## 커밋 4 — feat(chat): unified conversation files endpoint (M3) ✅ ba78cd06
- [x] `GET /api/conversations/{id}/files` (생성+첨부 머지, FileItem, source 태그, created_at 정렬, 소유 가드)
- [x] 라우트 충돌 없음 확인(/files vs /files/{path}) — 회귀 36 통과
- [x] pytest: 머지/정렬/source/권한, unsent 제외, 타유저 404
- [x] green → commit (백엔드 전체 2473 passed)

## 커밋 5 — feat(chat): render user attachments inline (프론트) ✅ 8170b392
- [x] UserMsg에 인라인 첨부 렌더 + attachment-to-artifact 어댑터 + 프리뷰 다이얼로그
- [x] vitest + tsc + lint green → commit

## 커밋 6 — feat(chat): file list badge + jump-to-message (프론트) ✅ e35d7031
- [x] /files API 클라이언트 + FileItem 타입 + useConversationFiles 훅
- [x] 레일: 생성=chatArtifactsAtom(라이브 유지) + 첨부=/files 하이브리드(회귀 0), 생성/첨부 배지, read-only
- [x] jumpToMessage(useSyncExternalStore+MutationObserver, 가상화 없음 DOM 앵커)
- [x] i18n chat.files.* ko/en
- [x] vitest 1079 + tsc + lint green → commit

## 커밋 7 — test(e2e) ✅
- [x] throwaway 스택(:5433/3100/8101) E2E 2/2 green: 전송→인라인→reload→messages echo→/files→다운로드, 공유 제외+/files auth

## E2E가 발견한 수정 (commits 69e6e67f, 275a913c)
- [x] 백엔드: v3 chat은 agent-protocol run.start로 send → attachment_ids를 worker에 threading 누락 → backfill 안 됨. 수정 + 단위 테스트.
- [x] 프론트: v3 런타임은 LangGraph state 메시지라 s.message.attachments 비어 있음 → MessagePrimitive.Attachments 미작동. message-id로 /files 조회하는 데이터 주도 렌더로 수정.

## 최종
- [x] DoD 충족, 회귀 없음 (백엔드 2473 / 프론트 vitest 1078 / tsc / lint / E2E 2/2)
- [ ] push (pre-push 막히면 SKILL_EVALUATION_ENABLED=true)

## 후속 수정 (1·2a·2b 이번에 처리)
- [x] (1) 첨부만 있는 대화용 "파일" 버튼 — composer 툴바에서 파일 패널(list) 열기
- [x] (2a) run 완료 시 /files invalidate → 라이브 전송 첨부가 답변 끝나면 바로 인라인 표시
- [x] (2b) 생성 FileItem.message_id = linked_message_ids[0](실제 메시지 id) → 생성 파일 jump 정확

## TODO (꼭 해야 함, 이번 범위 아님)
- [ ] **O2 업로드 암호화**: 첨부·생성 파일 둘 다 디스크에 평문 저장(shutil.copy2 / write_bytes). API는 인증 보호됨.
      정책 결정(전체 / 민감 MIME만 / 안 함) 후 첨부+생성에 일괄 적용. 첨부만의 신규 약점은 아님(기존 생성 파일과 동일).
