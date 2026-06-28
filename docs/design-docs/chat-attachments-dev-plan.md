# 채팅 첨부 파일 — 개발 기획서 (구현 준비)

> 상태: 기획 확정 진행 중. 배경/대안 분석은 `chat-attachments-analysis.md` 참조.
> 이 문서는 **무엇을 어떻게 만들지**(파일 단위 작업 + 테스트 + 마일스톤)를 정의한다.
> 근거: 실제 소스 분석(backend/frontend) + LangChain 1.x(`langchain_core 1.4.7`) + assistant-ui 0.14.18.
> 라인 번호는 분석 시점 기준 근사치(`~`)이며 구현 시 재확인한다.

---

## 1. 목표 / 비목표

### 목표
- **(표시)** 사용자가 보낸 첨부 파일을 전송 후에도 볼 수 있다: ① 보낸 메시지(user 버블)에 인라인, ② 대화의 통합 "파일" 리스트(생성 파일과 함께, `첨부` 배지로 구분).
- **(프리뷰)** 지원 포맷은 기존 아티팩트 프리뷰로 보고, 미지원 포맷은 "지원하지 않음 + 다운로드"로 처리(기존 fallback 재사용).
- **(맥락)** 파일에서 그 파일이 붙은 대화 메시지로 이동(로드된 메시지에 한해).
- **(보안)** 첨부 파일 접근을 인증·소유권으로 보호.
- **(모델 입력, 후속)** 에이전트가 첨부(이미지→문서)를 실제로 인지(multimodal).

### 비목표 (이번 범위 밖, 후속 phase)
- 첨부 직접 편집(첨부는 read-only). "복사 → 생성 아티팩트 → 편집"은 **P2+**.
- 업로드 암호화(정책 결정 후 별도).
- 페이지네이션 너머 옛 메시지로의 fetch-and-scroll 이동(이번엔 라벨만).

---

## 2. 현재 상태 (소스 기반 요약)

### 백엔드
| 영역 | 현재 | 파일 |
|---|---|---|
| 업로드 | `POST /api/uploads` → `message_attachments` 행 생성(평문 디스크 `./data/uploads/`), `message_id`·`conversation_id` null. 화이트리스트 image/text/pdf/json, 20MiB | `routers/uploads.py:~67-108` |
| 다운로드 | `GET /api/uploads/{id}` **인증/소유권 체크 없음** | `routers/uploads.py:~111-133` |
| 전송 시 연결 | `link_attachments_to_conversation`이 `conversation_id`만 세팅(`message_id` null 유지) | `services/chat_service.py:~1221-1247`, 호출부 `routers/conversation_messages.py:~73-79, ~399-405` |
| 메시지 읽기 hydration | `WHERE conversation_id=c AND message_id IS NOT NULL` → message_id가 null이라 **0건 → 첨부 누락** | `services/chat_service.py:~1102-1120` |
| 응답 스키마 | `MessageResponse.attachments: list[MessageAttachmentBrief] | None` (id/filename/mime_type/size_bytes/url) | `schemas/conversation.py:~121-135, ~183` |
| 모델 입력 | `input_payload=[{"role":"user","content":data.content}]` → `convert_to_langchain_messages` → `HumanMessage(content=<str>)`. **첨부는 모델에 전달 안 됨** | `routers/conversation_messages.py:~434`, `agent_runtime/message_utils.py:~167-178`, `runtime_component_builder.py:~746` |
| message_id 타이밍 | HumanMessage id는 서버 생성, **런 종료 후** 확정. 클라 지정 불가. backfill 훅 후보 = `finalize_turn` | `services/trace_storage.py: finalize_turn` |
| 생성 파일 | `conversation_artifacts` + `artifact_versions`(M59), `assistant_msg_id`+`logical_path` 연결, 버전관리 | `models/conversation_artifact.py` |

### 프론트
| 영역 | 현재 | 파일 |
|---|---|---|
| 업로드 어댑터 | `MoldyAttachmentAdapter`(accept=image/text/pdf/json), `send()`가 업로드 후 `[attachment: name](url)` 마크다운 링크로 변환 | `lib/chat/attachment-adapter.ts` |
| 메시지 변환 | 백엔드 `message.attachments` → assistant-ui `CompleteAttachment` 변환 **이미 존재** | `lib/chat/convert-message.ts:~49-61` |
| user 버블 | `MessagePrimitive.Content`만 렌더. **`MessagePrimitive.Attachments` 미사용 → 전송 후 첨부 안 보임** | `components/chat/assistant-thread.tsx` UserMsg(~600+) |
| composer staging | `ComposerPrimitive.Attachments` + `AttachmentChip` + `AddAttachment`(paperclip) | `assistant-thread.tsx:~1124-1135, ~1209-1242` |
| 생성 파일 인라인 | `AssistantArtifactCards`(assistant 메시지 하단 카드) | `assistant-thread.tsx:~355-410` |
| 생성 파일 레일/라이브러리 | `ArtifactPanelContent`(레일), `ArtifactLibraryContent`(라이브러리) | `right-rail/artifact-panel-content.tsx`, `components/artifacts/artifact-library-content.tsx` |
| 프리뷰 registry | 16종 provider(image/pdf/docx/xlsx/pptx/hwp/mermaid/markdown/code/json/table/text/...) + fallback("미지원→아이콘+다운로드") | `components/chat/artifacts/preview-registry.tsx` + `providers/`, dispatch=`artifact-preview.tsx:~15-72` |
| 정규 타입 | `ArtifactSummary`(id/path/display_name/mime_type/extension/artifact_kind/preview_url/download_url/...) | `lib/types/artifact.ts` |

### assistant-ui 0.14.18 제공 API
- `MessagePrimitive.Attachments components={{ Image, Document, File, Attachment }}` (user 메시지 첨부 렌더, composer와 동일 shape)
- `AttachmentPrimitive.Root/Name/Remove/unstable_Thumb`, `useAttachmentSrc()` (썸네일), 클릭→프리뷰 다이얼로그 패턴

---

## 3. 설계 결정 (확정 / 미정)

| # | 결정 | 값 | 상태 |
|---|---|---|---|
| D1 | 스코프 | 단계적: **Phase 0 보안 → P1 표시 → P2+ 모델입력 → P2+ 복사편집** | ✅ 확정 |
| D2 | 모델입력 방식 | (P2) 이미지=multimodal block, 문서=텍스트추출/RAG. provider 능력 게이팅 | ✅ 방향 확정 |
| D3 | 지원 타입 | 이미지 먼저 → PDF → office(텍스트추출) | ✅ 확정 |
| D4 | provider 게이팅 | 능력 매트릭스로 게이팅, 미지원 모델이면 **모델 주입 skip(표시만)** | ✅ 확정 |
| D5 | 표시 UX | **인라인(user 버블) primary + 통합 리스트 secondary**, `첨부` 배지로 구분, 클릭→`ArtifactPreview` 재사용, 첨부 **read-only** | ✅ 확정 |
| D6 | 보안 | `GET /api/uploads/{id}`에 인증 + 소유권(+공유링크 읽기) | ✅ 확정 (암호화는 보류) |
| D7 | orphan GC | 미전송 업로드 24h 후 정리 cron | ✅ 확정(기본값) |
| D8 | message_id 연결 | `finalize_turn` 훅에서 backfill | ✅ 확정 |
| D9 | 파일→메시지 이동 | **로드된 메시지만** scroll+highlight, 아니면 "이전 메시지" 비활성 라벨 | ✅ 확정(간소화판, P1 포함) |
| D10 | PR/커밋 구성 | **Phase 0 + P1을 한 PR**, 단계별로 **커밋 분리**(P0 보안 → 백엔드 표시 → 프론트 표시 → 테스트) | ✅ 확정 |
| D11 | 공유 대화 정책 | 공유 뷰는 **첨부도 생성 파일도 모두 미노출**(공유 스냅샷에서 제외) | ✅ 확정 |
| D12 | 통합 리스트 데이터 | **백엔드 통합 엔드포인트** `GET /api/conversations/{id}/files` 하나로 생성+첨부를 정규화 반환(단일 소스). 레일이 이걸 사용, 라이브러리도 동일 소스로 확장. *프론트 머지(표면별 중복)는 채택 안 함.* 리스크: 기존 아티팩트 레일의 데이터 소스 교체 → 회귀 주의 | ✅ 확정 |
| D13 | DB 스키마 | **마이그레이션 불필요** — `message_attachments.message_id` 컬럼이 이미 존재(현재 null로 방치). 채우기만 함 | ✅ 확정 |
| O2 | 업로드 암호화 | 전체/민감 MIME만/안 함 | ⛳ 보류(후속) |
| O3 | P2(모델입력) 착수 시점 | P1 출시 후 별도 | ⛳ 후속 |

---

## 4. 아키텍처 / 데이터 흐름 (P1, Path A = message-scoped)

```
[전송]
 composer 업로드 → POST /api/uploads → message_attachments(row, message_id=null)
 send_message(data.content, attachments[])
   → link_attachments_to_conversation (conversation_id 세팅)
   → run 시작 → 스트림 → 런 종료(finalize_turn)
       └─ (신규) user HumanMessage id 확정 → 해당 attachment rows.message_id 백필   ← D8

[읽기/렌더]
 GET /messages
   → hydration (message_id 채워졌으니 echo)  ← 기존 쿼리 그대로 동작
   → MessageResponse.attachments[]
   → convert-message → CompleteAttachment[]
   → (신규) UserMsg: <MessagePrimitive.Attachments components={...}>  ← 인라인 primary  (D5)
   → (신규) 통합 파일 리스트(레일): GET /api/conversations/{id}/files(생성+첨부 정규화, 배지) ← secondary (D5/D12)
        클릭 → ArtifactPreview 재사용(지원/미지원)                      ← 프리뷰        (D5)
        "대화로 이동" → message_id로 scroll(로드시)/라벨(미로드)        ← navigation   (D9)

[보안]  GET /api/uploads/{id} → 인증 + 소유권/공유 검사  ← Phase 0 (D6)
```

핵심: **첨부는 message-scoped**(D8)라 reload/공유/브랜치에서 정확. 통합 리스트는 **백엔드 `/files` 엔드포인트**(D12)가 두 소스를 정규화 머지한 단일 응답을 제공(프론트 표면별 머지 X).

---

## 4.5 핵심 메커니즘 & 데이터 계약 (구현 명세 — 검증 완료)

### M1. message_id 연결 — **Option B 확정 (post-run 백필)**
- HumanMessage id는 send 시 moldy가 제어 불가(`convert_to_langchain_messages`가 `HumanMessage(content=...)`만 생성, id 미지정 → checkpointer 생성). ∴ send-time id(Option A) 불가.
- 위치: `services/conversation_stream_service.py::finalize_trace()`(스트림 종료 후)에서 이번 턴 **user HumanMessage id**를 해석해 이번 send 첨부에 연결.
- **id 매칭 규칙**: `list_messages_from_checkpointer`는 user 메시지 id를 `parse_msg_id(msg.id, conversation_id, idx)`(`message_utils.py:111`, `parse_msg_id:24-30`)로 만든다 → 백필도 **동일 규칙**으로 같은 UUID 생성해야 hydration 매칭.
- ⚠️ **주의(구현 크럭스)**: `msg_id_sink[0]`/`idx=0`을 user 메시지로 단정하지 말 것. post-run 체크포인트 상태에서 **이번 턴에 추가된 HumanMessage**를 정확히 식별(마지막 HumanMessage 또는 content 매칭)하고 그 (id, idx)로 parse.
- 신규 헬퍼(이번 send의 attachment_ids만 연결 → 크로스-send 오연결 방지):
  ```python
  async def link_attachments_to_message(db, *, attachment_ids, message_id: str) -> None:
      await db.execute(update(MessageAttachment)
          .where(MessageAttachment.id.in_(attachment_ids),
                 MessageAttachment.message_id.is_(None))
          .values(message_id=message_id))
  ```
- attachment_ids는 send 시 run/turn 컨텍스트(run 레코드 또는 stream 컨텍스트)에 실어 finalize까지 전달.

### M2. 공유 제외(D11) — **확정 (단일 함수 게이팅)**
- 공유 읽기는 인증과 **같은** `list_messages_from_checkpointer(db, conversation, user_id=None)` 사용(`routers/shares.py` `GET /api/shares/{token}/messages`).
- 현재: 첨부 hydration(`chat_service.py:~1102-1120`)은 user_id 게이팅 **없음** → message_id 채우면 공유에 첨부 노출. 아티팩트(`~1122-1136`)는 이미 `if user_id is not None` 게이팅 → 공유 제외됨.
- 변경: 첨부 hydration도 `if user_id is not None:`로 감싼다 → 공유(user_id=None)는 **첨부·생성 파일 둘 다 미노출**. (생성 파일은 추가 변경 불필요.)

### M3. 통합 파일 엔드포인트(D12) — `GET /api/conversations/{id}/files`
- 소유/참여 가드. `conversation_artifacts`(생성) + `message_attachments`(첨부, message_id 있는 것) 머지, `created_at` 정렬, `source` 태그.
- `FileItem` 정규 shape (프론트/백엔드 공통):
  ```ts
  type FileItem = {
    source: 'generated' | 'attached'
    id: string
    name: string            // display_name | filename
    mime_type: string
    extension?: string
    kind?: string           // artifact_kind (generated)
    size_bytes?: number
    preview_url: string     // generated=artifact preview_url / attached=/api/uploads/{id}
    download_url: string
    message_id?: string      // attached→user 메시지 / generated→assistant_msg_id
    created_at: string
    editable: boolean        // generated=true, attached=false (read-only, D5)
  }
  ```
- 프론트는 인라인용 `MessageAttachmentBrief`와 리스트용 `FileItem` 모두 기존 `ArtifactPreview` registry로 dispatch(미지원→fallback "미리보기 지원 안 함 + 다운로드").

### M4. 파일→메시지 이동(D9)
- 각 메시지 버블에 `data-moldy-message-id={id}` 앵커 추가(없으면).
  ```ts
  function jumpToMessage(messageId?: string) {
    if (!messageId) return
    const loaded = runtimeMessages.some(m => m.id === messageId)  // state 기준(가상화 안전)
    if (loaded) {
      document.querySelector(`[data-moldy-message-id="${messageId}"]`)?.scrollIntoView({ block: 'center' })
      highlight(messageId)
    } else { /* "이전 메시지" 비활성 라벨/툴팁 */ }
  }
  ```

### M5. i18n 신규 키 (ko/en)
`chat.files.attachedBadge`("첨부"), `chat.files.generatedBadge`("생성"), `chat.files.jumpToMessage`("대화로 이동"), `chat.files.notInLoaded`("이전 메시지"), `chat.files.unsupportedPreview`("미리보기를 지원하지 않습니다").

---

## 5. Phase별 작업 (파일 단위 체크리스트)

### Phase 0 — 보안 (선행 권장, S~M)
- [ ] **업로드 접근 가드**: `routers/uploads.py` `GET /api/uploads/{id}`에 `Depends(get_current_user)` + 소유권 검사(`MessageAttachment.user_id == user.id` 또는 대화 참여자). 같은 출처 쿠키라 `<img src>`도 정상 동작.
- [ ] **공유 링크 케이스**: 공유 대화 뷰어가 첨부를 봐야 하면 share 토큰 기반 읽기 허용 경로 추가(또는 P1에서 공유는 첨부 숨김으로 단순화 — 결정 필요).
- [ ] **orphan GC**: `message_id IS NULL AND created_at < now()-24h` 인 업로드 정리 APScheduler 잡(기존 스케줄러에 등록). 저장 파일도 삭제.
- [ ] 테스트: 비인증/타유저 다운로드 403/404, 본인 200, 공유 토큰 케이스, GC 잡 단위 테스트.

### P1 — 표시 (M, 4~6일)

**백엔드** (마이그레이션 없음 — D13)
- [ ] **message_id 연결(D8)** — §4.5의 메커니즘 사용. 두 후보 중 구현 시 확정(검증 중):
  - **A (권장, send-time id)**: send 시 user 메시지 id를 moldy가 UUID로 생성·HumanMessage에 부여하고, `link_attachments_to_conversation`에서 같은 id를 `message_attachments.message_id`로 세팅. checkpointer round-trip에 id 보존되면 post-run 백필 불필요.
  - **B (fallback, post-run 백필)**: A가 불가하면 `finalize_turn`(`services/trace_storage.py`)에서 이번 턴 user HumanMessage id를 해석해 이번 send의 attachment_ids에 UPDATE. (attachment_ids를 run/turn 컨텍스트로 전달)
- [ ] hydration(`chat_service.py:~1102-1120`)은 기존 쿼리 유지(이제 매칭됨). 누락 시 보완.
- [ ] **공유 제외(D11)**: 공유 스냅샷 빌더에서 메시지의 `attachments`와 생성 파일(artifacts)을 **제외**. (§8 — 현재 공유가 생성 파일을 노출하는지 확인 후, 노출되면 제거하는 정책 변경 포함.) 통합 리스트(레일/라이브러리)는 공유 뷰에서 미노출.
- [ ] **통합 파일 엔드포인트(D12)**: `GET /api/conversations/{id}/files` — `conversation_artifacts`(생성) + `message_attachments`(첨부)를 §4.5 `FileItem`으로 정규화 머지(생성일 정렬), `source` 태그. 권한은 대화 소유/참여 가드. 인라인(per-message)은 별도로 `MessageResponse.attachments` echo 사용(이 엔드포인트와 무관).
- [ ] 테스트: 첨부 전송→GET /messages echo, `/files`가 생성+첨부 정규화 반환·정렬·source, reload 유지, 브랜치/멀티 전송 메시지별 정확 매핑, 공유 뷰에서 첨부·생성 파일 미노출.

**프론트**
- [ ] **인라인 렌더(D5, primary)**: `UserMsg`(`assistant-thread.tsx`)에 `<MessagePrimitive.Attachments components={{ Image, Document, File, Attachment }}/>` 추가. 이미지=`unstable_Thumb`+`useAttachmentSrc()` 썸네일, 문서=파일 칩. 클릭 → 프리뷰.
- [ ] **첨부 프리뷰 어댑터**: `CompleteAttachment`/`MessageAttachmentBrief` → `ArtifactSummary`류 정규 shape 변환(id/filename/mime/extension/preview_url=download_url=`/api/uploads/{id}`/`source:'attached'`). 기존 `ArtifactPreview` registry로 dispatch(지원/미지원 fallback 자동).
- [ ] **통합 파일 리스트(D5, secondary)**: `ArtifactPanelContent`(레일)이 **`GET /files`(D12)를 소비**하도록 전환(기존 artifacts-only 소스 교체 — 회귀 주의). 각 항목에 **`첨부`/`생성` 배지**(또는 그룹 헤더 "내가 보낸 파일"/"생성된 파일"). 클릭 → 동일 `ArtifactPreview`. *전역 `ArtifactLibraryContent` 통합은 후속.*
- [ ] **첨부 read-only(D5)**: 첨부 항목은 edit/save 액션 미노출, preview·download·"대화로 이동"만.
- [ ] **파일→메시지 이동(D9)**: 리스트/프리뷰에 "대화로 이동" 액션. 대상 message_id가 **현재 로드된 메시지 set에 있으면** 해당 버블로 scroll+highlight(메시지 버블에 `data-moldy-message-id` 앵커 추가), 없으면 "이전 메시지" 비활성 라벨/툴팁. (가상화 여부 확인 후 state 기준 판정)
- [ ] 테스트(vitest): 인라인 렌더(이미지/문서), 미지원 포맷 fallback, 통합 리스트 배지 구분, read-only(편집 버튼 없음), 이동 액션(로드/미로드 분기).

**E2E**
- [ ] 첨부(이미지+문서) 전송 → user 버블 인라인 표시 → reload 후 유지 → 통합 리스트에 `첨부` 배지로 노출 → 프리뷰 열림 → "대화로 이동" 동작 → 다운로드 링크.

### P2+ — 모델 입력: 이미지 (M~L, 후속)
- [ ] 메시지 조립 경로에서 이미지 첨부를 LangChain `image` content block으로 주입: `HumanMessage(content_blocks=[{type:'text',...},{type:'image',base64,mime_type}])`. 위치 = `messages_history` 생성/`convert_to_langchain_messages` 상류.
- [ ] **provider 능력 게이팅(D4)**: `(provider, model)`이 vision 지원일 때만 주입, 아니면 표시만(주입 skip). Hancom 게이트웨이/openai_compatible은 보장 안 됨 → 보수적 처리.
- [ ] 토큰/compaction 영향 확인(media가 prompt 토큰 폭증 → context-window 프로파일 연동).
- [ ] 테스트: vision 모델 주입/비전 미지원 skip, 토큰 카운트.

### P2+ — 문서 모델 입력 (L, 후속)
- [ ] PDF → `file` content block(provider별: Anthropic document / OpenAI base64+filename / Google PDF). URL 파일은 OpenAI Chat Completions 거부 주의.
- [ ] office/csv → 네이티브 block 없음 → 서버 텍스트 추출(python-docx/openpyxl 등) 또는 기존 RAG read-tool 패턴 재사용.
- [ ] 대용량/재사용 → Files API `file_id` 참조로 재전송 회피.

### P2+ — 복사 → 편집 (M, 후속)
- [ ] "이 첨부를 작업 파일로 복사" 액션: `message_attachments` 바이트 → `conversation_artifacts` 복제(새 행/버전) → 편집 가능 lifecycle 진입. 원본 첨부는 불변 유지.

---

## 6. LangChain multimodal 레퍼런스 (P2 구현용)

LangChain 1.x 표준 content block (`langchain_core/messages/content.py`):
- 이미지 `ImageContentBlock(:~498)`: `{type:'image', base64|url, mime_type}`
- 파일/PDF `FileContentBlock(:~721)`: `{type:'file', base64|url, mime_type}` (docx/PDF 등 비이미지)
- 평문 `PlainTextContentBlock(:~651)`: `{type:'text-plain', text}`
- 생성: `HumanMessage(content_blocks=[...])` (`messages/human.py:43-56`) 또는 factory `create_image_block`/`create_file_block`.

provider 매트릭스:
| provider | 이미지 | 파일/PDF | 주의 |
|---|---|---|---|
| OpenAI | image_url | base64/file_id | **파일 URL 거부(Chat Completions)**, 파일명 필요 |
| Anthropic | ✅ | document(PDF/text/url) | |
| Google | inline_data | PDF base64/file_uri | |
| OpenRouter/compat/Hancom | 모델 의존 | 모델 의존 | **보장 안 됨 → 게이팅 필수** |

- 미지원 block → `ValueError`(silent fallback 없음). deepagents는 통과시키나 media가 토큰/compaction에 영향. `model_factory`는 model 구성만 — 주입은 메시지 조립부.

---

## 7. 테스트 계획 (요약)
- **백엔드**: 업로드 가드(403/404/200/share), GC 잡, message_id 백필, hydration echo, 브랜치 매핑. (pytest aiosqlite)
- **프론트**: 인라인 렌더, 미지원 fallback, 통합 리스트 배지, read-only, 이동 분기. (vitest)
- **E2E**: 전송→인라인→reload→리스트→프리뷰→이동→다운로드. (Playwright, throwaway 스택)
- 회귀: 기존 아티팩트 렌더/프리뷰/레일 무영향, 채팅 E2E sweep green.

---

## 8. 리스크 / 미해결 질문
**해소된 결정** (위 D표 참조): PR 구성(D10, P0+P1 한 PR/커밋 분리), 공유 정책(D11, 첨부·생성 모두 미노출 — M2), 통합 리스트 데이터(D12, 백엔드 `/files` 엔드포인트), 마이그레이션(D13, 없음), message_id(M1, post-run 백필).

**남은 리스크 / 후속**
- **(구현 크럭스, M1)**: 이번 턴 user HumanMessage id 정확 식별 — `msg_id_sink[0]`/`idx=0` 단정 금지. 단위 테스트로 멀티턴·브랜치 매핑 검증 필수.
- **레일 데이터 소스 교체(D12)**: 기존 아티팩트 레일이 `/files`로 옮겨가므로 기존 아티팩트 렌더 회귀 주의 + 회귀 테스트.
- **가상화**: transcript 가상화 시 "로드 판정"은 DOM 아닌 runtime state 기준(M4).
- **provider 게이팅(P2)**: system LLM이 Hancom 게이트웨이 → vision 미보장. 미처리 시 런 실패(`ValueError`) — 능력 게이팅 필수.
- **토큰/비용(P2)**: base64 media가 prompt 토큰 폭증 → context-window/compaction 연동.
- **O2 암호화(보류)**: 정책 미정(후속).

---

## 9. 마일스톤 / 분량
| 단계 | 범위 | 분량 |
|---|---|---|
| Phase 0 | 업로드 인증/소유권/공유 + orphan GC | S~M |
| **P1** | message_id 백필 + 인라인 + 통합리스트(배지) + read-only + 파일→메시지 이동(간소화) | **M (4~6일)** |
| P2 (이미지) | image content block 주입 + 게이팅 + 토큰 | M~L |
| P2 (문서) | PDF/office/RAG/file_id | L |
| P2+ | 복사→편집 | M |

**①표시(Phase 0 + P1) 합계 ≈ 5~8일.** ②모델입력 풀은 추가 L~XL(1.5~3주).

---

## 10. PR / 커밋 계획 (D10 — Phase 0 + P1 한 PR, 커밋 분리)
브랜치 예: `feature/chat-attachments-display`. 커밋 순서(각 커밋은 그 자체로 그린):
1. `fix(security): authn+ownership guard on GET /api/uploads/{id}` (+ orphan GC 잡) — Phase 0
2. `feat(chat): backfill message_attachments.message_id on turn finalize` — M1 (백엔드 + 단위테스트)
3. `feat(chat): gate attachment hydration to authed views (exclude from shares)` — M2
4. `feat(chat): unified conversation files endpoint (generated + attached)` — M3 `/files` + FileItem
5. `feat(chat): render user attachments inline on the message bubble` — 프론트 인라인(MessagePrimitive.Attachments) + 프리뷰 어댑터
6. `feat(chat): show attachments in the file list with a badge + jump-to-message` — 통합 리스트(레일) + read-only + M4 이동 + i18n
7. `test(e2e): attachment send → inline → reload → list → preview → jump` — E2E

## 11. 수용 기준 (Definition of Done)
- 비인증/타유저가 `GET /api/uploads/{id}` 접근 → 거부. 본인/참여자만 200.
- 미전송 업로드는 GC로 정리됨(24h).
- 이미지·문서 첨부 전송 → **user 버블에 인라인** 표시(이미지=썸네일, 문서=칩) → **reload 후 유지**.
- 통합 파일 리스트(레일)에 생성+첨부가 함께, `첨부`/`생성` **배지로 구분**, 클릭 시 프리뷰(지원 포맷 렌더 / 미지원 "지원 안 함 + 다운로드").
- 첨부는 **edit 불가**(preview·download·이동만).
- 파일에서 "대화로 이동" → 로드된 메시지면 스크롤+하이라이트 / 아니면 "이전 메시지" 라벨.
- **공유 뷰에서 첨부·생성 파일 모두 미노출.**
- 멀티턴/브랜치에서 첨부가 **올바른 user 메시지**에 매핑.
- 회귀: 기존 아티팩트 레일/프리뷰/라이브러리 정상, vitest·채팅 E2E sweep green.

## 12. 착수
모든 핵심 결정 확정됨(O2 암호화만 보류). 위 커밋 1번(보안)부터 순서대로 진행 가능. 구현 첫 스텝에서 `conversation_stream_service.finalize_trace` + `parse_msg_id` 경로를 열어 M1의 "이번 턴 user 메시지 id 식별"을 확정한 뒤 진행.
