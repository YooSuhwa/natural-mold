# 채팅 첨부 파일 표시 — Phase 0 + P1 (todo)

Source of truth: `docs/design-docs/chat-attachments-dev-plan.md`
Plan: `~/.claude/plans/frolicking-marinating-quilt.md`
범위: Phase 0(보안) + P1(표시). **P2(모델 multimodal)는 제외.** 커밋 7개 분리.

## 커밋 1 — fix(security): authn+ownership guard on GET /api/uploads/{id} (+ orphan GC)
- [ ] `get_upload`에 `Depends(get_current_user)` + 소유권(`row.user_id == user.id`), 미존재/권한없음 모두 404
- [ ] orphan GC 잡(message_id IS NULL AND created_at < now()-24h → 행+디스크 삭제) 스케줄러 등록
- [ ] pytest: 비인증/타유저 거부, 본인 200, GC 단위
- [ ] `uv run pytest` + `uv run ruff check .` green → commit

## 커밋 2 — feat(chat): backfill message_attachments.message_id on turn finalize (M1)
- [ ] 검증 테스트 먼저: resolver id == list_messages_from_checkpointer user id (add_messages UUID 가정)
- [ ] `resolve_turn_user_message_id(db, conversation)` (트리 마지막 human → parse_msg_id)
- [ ] `link_attachments_to_message(db, *, attachment_ids, message_id)` (WHERE id IN ids AND message_id IS NULL)
- [ ] threading: start_conversation_run → _run_conversation → finalize 직후 backfill
- [ ] pytest: 단일 echo, 멀티턴/브랜치 매핑, 빈 무동작
- [ ] green → commit

## 커밋 3 — feat(chat): gate attachment hydration to authed views (M2)
- [ ] 첨부 hydration(chat_service.py:1102-1120)을 `if user_id is not None:`로 감쌈
- [ ] pytest: 인증 echo / 공유 미노출
- [ ] green → commit

## 커밋 4 — feat(chat): unified conversation files endpoint (M3)
- [ ] `GET /api/conversations/{id}/files` (생성+첨부 머지, FileItem, source 태그, created_at 정렬, 소유 가드)
- [ ] 라우트 충돌 없음 확인(/files vs /files/{path})
- [ ] pytest: 머지/정렬/source/권한
- [ ] green → commit

## 커밋 5 — feat(chat): render user attachments inline (프론트)
- [ ] (전제) frontend pnpm install + assistant-ui 타입 확인
- [ ] UserMsg에 MessagePrimitive.Attachments; convert-message image content part 보정
- [ ] attachment-to-artifact 어댑터 + 프리뷰 클릭
- [ ] vitest + tsc + lint green → commit

## 커밋 6 — feat(chat): file list badge + jump-to-message (프론트)
- [ ] /files API 클라이언트 + FileItem 타입 + useConversationFiles 훅
- [ ] 레일 데이터 소스 교체(회귀 주의) + 생성/첨부 배지 + read-only
- [ ] data-moldy-message-id 앵커 + jumpToMessage(state 기준)
- [ ] i18n chat.files.* ko/en
- [ ] vitest(+생성 레일 회귀) + tsc + lint green → commit

## 커밋 7 — test(e2e)
- [ ] throwaway 스택으로 전송→인라인→reload→리스트→프리뷰→이동→다운로드 + 공유 미노출
- [ ] green → commit

## 최종
- [ ] §11 DoD 전부 충족, 기존 아티팩트 레일/프리뷰/라이브러리 회귀 없음
- [ ] push (pre-push 막히면 SKILL_EVALUATION_ENABLED=true)
