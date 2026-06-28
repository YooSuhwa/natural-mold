# 채팅 첨부 파일 — 개발 기획 분석 (결정 전 단계)

> 목적: "첨부 파일을 제대로 처리"하기 위해 **고려해야 할 것**, **결정해야 할 것**, **작업 분량**을 사전 분석한다. 구현 결정은 이 문서의 "결정 항목"을 사용자가 정한 뒤 별도 SPEC/plan으로 진행한다.
> 근거: backend/frontend 소스 분석 + LangChain 1.x(`langchain_core 1.4.7`) 소스 + assistant-ui 0.14.18 + assistant-ui 첨부 문서.

---

## 0. 핵심 재정의 — "첨부 처리"는 두 개의 분리된 기능이다

| | 무엇 | 현재 상태 | 가치 |
|---|---|---|---|
| **① 표시(display)** | 사용자가 보낸 첨부를 transcript(user 버블)에 다시 보여줌 | ❌ 전송 후 사라짐 (composer staging만 존재) | "내가 뭘 보냈는지" UX |
| **② 모델 입력(ingestion)** | 에이전트가 첨부 파일 **내용**을 실제로 봄 (이미지/문서) | ❌ **0%** — 모델은 텍스트만 받음 | "에이전트가 첨부를 이해" — 진짜 기능 |

두 기능은 **독립적으로** 출시 가능하다. ①만 해도 가치가 있고(보낸 것 확인), ②는 ①과 별개의 백엔드 작업(메시지 조립 + provider 게이팅)이다. **가장 큰 결정은 "어디까지 할 것인가"다.**

---

## 1. 현재 상태 (확정)

### 백엔드
- **모델은 텍스트만 받는다.** `send_message` → `input_payload=[{"role":"user","content":data.content}]` → `convert_to_langchain_messages`(message_utils.py:167) → `HumanMessage(content=<string>)`. 첨부 파일은 디스크에서 읽히지도, content block으로 주입되지도 않음. 에이전트는 첨부 존재 자체를 모름.
- **첨부 저장**: `message_attachments` 행. 업로드 시 `message_id`·`conversation_id` null → 전송 시 `link_attachments_to_conversation`이 `conversation_id`만 세팅(`message_id`는 여전히 null).
- **읽기 누락**: GET /messages hydration이 `conversation_id == c AND message_id IS NOT NULL`로 필터 → message_id가 null이라 **0건 → 첨부가 응답에서 누락**.
- **보안 갭** (멀티유저에서 중요):
  - `GET /api/uploads/{id}` **인증 없음** — UUID만 알면 누구나 다운로드.
  - 업로드 파일 **암호화 안 됨** (평문 디스크 저장, `./data/uploads/`).
  - orphan(미전송) 업로드 **GC 잡 미구현**.
  - 화이트리스트: image/*, text/*, application/pdf, application/json, 20MiB.
- **message_id 타이밍**: HumanMessage id는 **서버 생성, 런 종료 후**에야 확정 → Path A는 `finalize_turn` 류 훅에서 backfill 필요. 클라이언트가 미리 지정 불가.

### 프론트
- **생성 파일(아티팩트)은 이미 풍부하게 렌더됨**: preview registry 16종(image/pdf/docx/xlsx/pptx/hwp/mermaid/markdown/code/json/table/...), 우측 레일, 라이브러리 페이지, assistant 메시지 인라인 카드(`AssistantArtifactCards`).
- **사용자 첨부는 전송 후 아무 데도 안 보임**. `convert-message.ts`는 `message.attachments` → assistant-ui `CompleteAttachment` 변환 코드를 **이미 보유**(백엔드가 echo만 하면 됨). 단 user 버블에 `MessagePrimitive.Attachments` **미사용**.
- assistant-ui 0.14.18: `MessagePrimitive.Attachments components={{ Image, Document, File, Attachment }}` + `useAttachmentSrc()` + `AttachmentPrimitive.unstable_Thumb`(썸네일) + 클릭→프리뷰 다이얼로그 패턴 제공.

---

## 2. LangChain 1.x 권장 방식 (②를 한다면)

**마크다운 링크 텍스트가 아니라 표준 content block으로 보낸다.** `HumanMessage(content_blocks=[...])`:
- **이미지** → `{type:"image", base64, mime_type}` (또는 url)
- **PDF** → `{type:"file", base64, mime_type:"application/pdf"}` (provider 지원 시)
- **.txt/.md** → `{type:"text-plain", text}`
- **docx/xlsx/pptx/csv 등** → **네이티브 block 없음** → 서버에서 텍스트 추출 후 주입

### provider 지원 매트릭스
| provider | 이미지 | PDF/파일 | 비고 |
|---|---|---|---|
| **OpenAI** | ✅ image_url | ✅ base64/file_id (**Chat Completions는 파일 URL 거부**, 파일명 필요) | |
| **Anthropic** | ✅ | ✅ document(PDF/text/url) | 파일을 document block으로 |
| **Google** | ✅ inline_data | ✅ PDF base64/file_uri | |
| **OpenRouter/openai_compatible/Hancom 게이트웨이** | ⚠️ 모델 의존 | ⚠️ 모델 의존 | **보장 안 됨** — 현재 system 모델이 Hancom 게이트웨이 |

- **미지원 block → `ValueError` (silent fallback 없음)** → 반드시 `(provider, model)` 능력으로 **게이팅** 필요.
- deepagents는 multimodal HumanMessage를 그대로 통과시킴. 단 **media는 토큰 수를 부풀려 auto-compaction 임계치에 영향**(context-window 작업과 연동).
- `model_factory`는 multimodal에 손 안 댐 — 작업은 **메시지 조립 경로**(messages_history 생성부)에 들어감.
- **대안(이미 존재)**: Assistant 에이전트는 multimodal 대신 **read-as-tool(RAG)** 패턴 사용(`list_agent_files`→`read_agent_file`→텍스트로 답). 문서 Q&A엔 이 방식도 유효하며 이미 배선됨.

---

## 3. 생성 파일 vs 첨부 파일 (UI에서 같이 표현?)

| | 첨부(입력) `message_attachments` | 생성(출력) `conversation_artifacts` (M59) |
|---|---|---|
| 누가 | 사용자 업로드 | 에이전트 도구(write_file 등) |
| 언제 | 전송 전 | 런 중/후 |
| 위치 | user 버블에 표시해야 함(목표) | 우측 레일/라이브러리/assistant 인라인 카드(이미 있음) |
| 버전 | 없음 | 버전 관리됨 |
| 프리뷰 | 없음(신규) | preview registry 16종(재사용 가능) |

**결정 필요**: 둘을 시각적으로 같게(통합 파일 UI) 갈지, 다르게(입력=중립/2차, 출력=주요) 갈지. 위치는 자연히 다름(user 버블 vs 레일).

---

## 4. 결정 항목 (사용자가 정해야 할 것)

| # | 결정 | 옵션 | 추천 |
|---|---|---|---|
| **D1. 스코프** | 어디까지? | (a) **표시만** / (b) 표시+모델입력(multimodal) / (c) 표시+모델입력(RAG read-tool) | **단계적**: 먼저 (a), 이후 (b) — 아래 phasing |
| **D2. 모델 입력 방식** | (b/c 선택 시) | multimodal content blocks vs 서버 텍스트 추출(RAG) | 이미지=multimodal, 문서=텍스트추출/RAG 혼합 |
| **D3. 지원 타입** | 무엇을 받나 | 이미지만 / +PDF / +office(docx·xlsx) | 이미지 먼저, PDF 다음 |
| **D4. provider 게이팅** | vision 미지원(Hancom 게이트웨이 등)일 때 | 텍스트추출 fallback / 첨부 거부+경고 / 그냥 표시만 | 능력 게이팅 + 미지원 시 표시만(모델 주입 skip) |
| **D5. 표시 UX** | user 버블 렌더 | 이미지 썸네일+파일 칩 / 클릭 시 프리뷰(레일 재사용 vs 라이트박스) / generated와 시각 구분 | 썸네일+칩, 클릭→기존 ArtifactPreview 레일 재사용 |
| **D6. 보안** | 업로드 접근/암호화 | GET 인증 추가? 암호화? | **인증은 사실상 필수**(멀티유저 취약점), 암호화는 정책 결정 |
| **D7. orphan GC** | 미전송 업로드 보존 | 24h/7d/… | 별도 cron, 24h 제안 |
| **D8. message_id 연결** | Path A 백필 시점 | finalize_turn 훅 | finalize_turn |

---

## 5. 작업 분량 / 단계 제안

> 각 phase는 독립 출시 가능. 권장 순서.

- **Phase 0 — 보안 (권장 선행, S~M)**: `GET /api/uploads/{id}` 소유권/인증 가드 + orphan GC 잡. *멀티유저에서 UUID-guessable 다운로드는 실제 취약점이라 표시 기능과 무관하게 우선.* (D6/D7)
- **Phase 1 — 표시 (M, 2~3일)**: 백엔드 `finalize_turn`에서 `message_attachments.message_id` backfill + hydration이 echo → 프론트 `MessagePrimitive.Attachments`로 user 버블 렌더(썸네일/칩) + 클릭 프리뷰(ArtifactPreview 재사용). 모델 입력 없이도 "보낸 첨부 보임" 완성. (D5/D8) — 백엔드+프론트+테스트+E2E.
- **Phase 2 — 모델 입력: 이미지 (M~L)**: 메시지 조립 경로에서 이미지 첨부를 `image` content block으로 주입 + `(provider,model)` vision 게이팅 + 토큰/compaction 영향 확인. (D2/D4) — 백엔드 중심.
- **Phase 3 — 문서 (L)**: PDF=file block(provider별), office=서버 텍스트 추출 또는 RAG read-tool, `file_id` 재사용. (D3) — 백엔드 + 추출 파이프라인.

**대략 총량**: ①표시까지(Phase 0+1) = **M (3~5일)**. ②모델입력 풀(Phase 2+3) = **추가 L~XL (1.5~3주)**, provider 게이팅·추출·토큰관리 때문.

---

## 6. 리스크 / 주의

- **보안**: 표시 기능을 켜면 첨부 URL이 더 노출됨 → Phase 0(인증) 선행이 안전.
- **provider 게이팅**: 현재 system LLM이 Hancom 게이트웨이라 vision 보장 안 됨 → 모델별 능력 매트릭스 + 미지원 시 graceful 처리 필수(미처리 시 `ValueError`로 런 실패).
- **토큰/비용**: base64 이미지·PDF는 prompt 토큰 폭증 → context-window/compaction과 연동, `file_id` 참조로 재전송 회피 고려.
- **데이터 모델**: 첨부는 message-scoped(Path A)로 가야 reload/공유/브랜치에 정확. conversation-scoped(Path B)는 매핑이 약해 비추.
