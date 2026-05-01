# Sheet 사용처 + 삭제 분석 — UI Refactor M-UI1 (S1, bezos)

> **NOTE**: Plan 모드 활성으로 인해 본 보고서는 plan 파일로 작성됨.
> 사티아가 실행 단계에서 본 내용을 `tasks/sheet-deletion-analysis.md`로 옮기고,
> AUDIT.log + progress.txt 갱신을 함께 수행하면 됨.
> (베조스의 분석 자체는 100% 완료)

---

## 1. Sheet 사용처 전수 (grep 결과)

| # | 파일경로:라인 | 종류 | 변환/유지 |
|---|---|---|---|
| 1 | `src/components/ui/sheet.tsx` | UI primitive (정의) | 유지 (sidebar에서 필요) |
| 2 | `src/components/ui/sidebar.tsx:177-196` | 모바일 사이드바 | **유지** (모바일 슬라이드 UX 적절) |
| 3 | `src/app/agents/[agentId]/conversations/[conversationId]/page.tsx:105-126` | 모바일 대화 목록 (`md:hidden`) | **유지** (모바일 좌측 슬라이드 UX 적절) |
| 4 | `src/components/credential/credential-detail-sheet.tsx:73-205` | Credential 상세 | **Dialog 변환** |
| 5 | `src/components/skill/skill-detail-sheet.tsx:36-46, 108-210` | Skill 상세 (이중 SheetContent) | **Dialog 변환** ⚠ |
| 6 | `src/components/tool/tool-detail-sheet.tsx:57-127` | Tool 상세 | **Dialog 변환** |
| 7 | `src/components/mcp/mcp-server-detail-sheet.tsx:95-182` | MCP 상세 | **Dialog 변환** |

**검증**: 위 7곳 외 Sheet 사용처 없음. PRD 가정과 일치.

---

## 2. Dialog 변환 대상 (4개) — 호출부 맵

### 2-1. credential-detail-sheet.tsx (219 lines)
- **호출부**: `src/app/credentials/page.tsx:14, 156`
- **prop 시그니처**: `{ credentialId: string | null, open: boolean, onOpenChange: (open: boolean) => void }`
- **호출부 패턴**:
  ```tsx
  <CredentialDetailSheet
    credentialId={detailId}
    open={!!detailId}
    onOpenChange={(open) => !open && setDetailId(null)}
  />
  ```
- **삭제 확인 인라인 UI**: line 179 — `border-destructive/40 bg-destructive/5 p-3`
- **위험**: Audit logs 섹션 포함 — Dialog 내 스크롤 영역 필요

### 2-2. skill-detail-sheet.tsx (212 lines) ⚠ 특이 케이스
- **호출부**: `src/app/skills/page.tsx:15, 185`
- **prop 시그니처**: `{ skillId, open, onOpenChange }` (위와 동일 패턴)
- **구조 특이**: 외부 `SkillDetailSheet` + 내부 `SkillDetailBody`(별도 컴포넌트, key reset 패턴) 두 개의 SheetContent.
  - 외부 (line 42): Loading placeholder
  - 내부 (line 108): 실제 본문 — `SkillDetailBody`는 별도 함수
- **삭제 확인 인라인 UI**: line 191
- **변환 시 주의**: Dialog로 감쌀 때 `key={skillId}` 패턴 보존 필요. `DialogContent`에 `sm:max-w-xl` 폭 유지.

### 2-3. tool-detail-sheet.tsx (130 lines)
- **호출부**: `src/app/tools/page.tsx:16, 155`
- **prop 시그니처**: `{ toolId, open, onOpenChange }`
- **삭제 확인 인라인 UI**: line 108
- **위험**: 가장 단순. 변환 가장 쉬움.

### 2-4. mcp-server-detail-sheet.tsx (242 lines) ⚠ prop 이름 다름
- **호출부**: `src/app/mcp-servers/page.tsx:16, 166`
- **prop 시그니처**: `{ serverId: string | null, open, onOpenChange }` — `serverId` (다른 3개는 `xxxId` 패턴이지만 의미 동일)
- **삭제 확인 인라인 UI**: line 163
- **추가 기능**: `useTestMcpServer`, `useDiscoverMcpTools` 호출 — Dialog 내에서 액션 영역 보존 필요

---

## 3. Sheet 유지 (2곳)

| 위치 | 이유 |
|------|------|
| `components/ui/sidebar.tsx:177` | 모바일 사이드바 — 좌측 슬라이드 인 패턴은 Sheet가 표준. Dialog 부적절. |
| `app/agents/[agentId]/conversations/[conversationId]/page.tsx:105` | `md:hidden` 모바일 전용 대화 목록 — 좌측 슬라이드 UX 유지 필요. |

→ `components/ui/sheet.tsx` primitive는 위 2곳 때문에 **반드시 유지**.

---

## 4. 즉시 삭제 가능 (Musk Step 2)

Dialog 변환 완료 후:
- 4개 detail-sheet 파일의 import에서 `Sheet`, `SheetContent`, `SheetHeader`, `SheetTitle`, `SheetDescription` 제거
- 파일명은 변환 PR에서 `*-detail-dialog.tsx`로 rename 권장 (사티아 결정)
- 호출부 4곳의 import 경로 + 컴포넌트명 동시 수정

**확인됨: 추가 dead code 없음.**

---

## 5. 삭제 검토 필요 (사티아 확인 필요)

### `components/shared/page-header.tsx` — **삭제 금지** ⚠
PRD에서 "사용처 0건"이라 했으나, **재검증 결과 7개 페이지에서 사용 중**:
- `src/app/settings/page.tsx:8,16`
- `src/app/settings/system-credentials/page.tsx:8,52`
- `src/app/tools/page.tsx:8,105`
- `src/app/agents/new/template/page.tsx:15,54`
- `src/app/usage/page.tsx:36,153`
- `src/app/models/page.tsx:17,349`
- `src/app/skills/page.tsx:10,95`
- `src/app/mcp-servers/page.tsx:11,130`
- `src/app/credentials/page.tsx:8,119`

→ **PageHeader는 활발히 사용 중. 삭제하면 9개 페이지가 깨진다.**
→ "?": PRD의 "PageHeader 사용처 0건" 분석은 어디서 나온 것인가? 다른 컴포넌트와 혼동된 것으로 추정.

---

## 6. 위험/주의사항

### 🔴 위험-A: skill-detail-sheet의 이중 SheetContent
- 외부 wrapper + 내부 `SkillDetailBody`로 분리된 구조 (line 36-46 + 108-210)
- 단순히 `Sheet→Dialog`로 sed 치환하면 **타입 에러 + key reset 동작 깨짐**
- 변환 시 `Dialog open={open} onOpenChange={...}` + 내부에 `<DialogContent>` 1개로 통합 필요
- `SkillDetailBody`의 `onClose={() => onOpenChange(false)}` 시그니처는 보존

### 🟡 위험-B: 컨텐츠 길이
- `mcp-server-detail-sheet.tsx` (242줄), `credential-detail-sheet.tsx` (219줄)는 폼 + 테스트 + 감사 로그 등 콘텐츠가 길다
- Dialog는 기본 max-h가 작으므로 **`max-h-[85vh] overflow-y-auto`** 명시 필요
- Sheet의 `sm:max-w-md` / `sm:max-w-xl` 폭은 그대로 Dialog에 옮기면 됨

### 🟡 위험-C: prop 시그니처 일관성
- 4개 모두 `{ <name>Id: string | null, open, onOpenChange }` 동일 패턴
- 다만 mcp는 `serverId`로 이름이 도메인 종속 — 변환 시 그대로 유지 권장 (호출부 변경 최소화)

### 🟢 안전: 삭제 확인 인라인 UI
- 4개 모두 `rounded border border-destructive/40 bg-destructive/5 p-3 text-xs` 패턴 동일
- Dialog 변환 시 동일 클래스 유지 — 회귀 위험 낮음
- (주의: text-xs는 OK이나 PRD 언급된 `text-[10px/11px]` 임의값은 본 4개 파일에서는 안 보임 — Sprint 2에서 별도 식별)

---

## 7. 변환 후 검증 커맨드

```bash
# 1. SheetContent가 mobile sidebar + conversation list 외 0건인지
cd frontend && grep -rn "SheetContent" src/components/ src/app/
#   → 기대 결과: ui/sheet.tsx (정의), ui/sidebar.tsx, app/agents/.../conversations/[id]/page.tsx 만 매치

# 2. 4개 detail-sheet 파일이 detail-dialog로 rename 되었는지
ls frontend/src/components/{credential,skill,tool,mcp}/*detail*

# 3. 호출부 4곳이 Dialog로 import되는지
grep -rn "DetailDialog" frontend/src/app/

# 4. 빌드 통과
cd frontend && pnpm build

# 5. 타입체크
cd frontend && pnpm exec tsc --noEmit
```

---

## 사티아에게 보고

**보고서 위치**: 본 plan 파일. 실행 단계에서 `tasks/sheet-deletion-analysis.md`로 옮길 것.

**핵심 위험 2가지**:
1. **PRD 오인 — `PageHeader` 삭제 절대 금지**: 9개 페이지에서 사용 중. PRD의 "사용처 0건" 분석은 잘못됐다. ("?" 필요)
2. **`skill-detail-sheet.tsx` 이중 SheetContent**: 단순 치환 불가. `SkillDetailBody` 분리 구조와 `key={skillId}` 리셋 패턴을 Dialog 변환 시 보존해야 회귀 없음.

**부가 정보**:
- 변환 대상 4개 파일 prop 시그니처 동일 패턴(`{xxxId, open, onOpenChange}`) — 호출부 변경 최소.
- mcp만 `serverId` (다른 3개는 `credentialId/skillId/toolId`) — 의도된 도메인 명명, 유지 권장.
- 삭제 확인 인라인 UI 4개 동일(`border-destructive/40 bg-destructive/5 p-3`) — 향후 `<DeleteConfirmInline>` 공용 컴포넌트로 추출 가능 (Sprint 2~3 후보).
