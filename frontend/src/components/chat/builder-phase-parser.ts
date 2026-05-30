/**
 * Builder phase narration 파서.
 *
 * 봇 텍스트에 dump되는 phase 전환 문구(`[Phase N 완료]`, `이제 Phase N: <단계명>을
 * 시작합니다` 등)를 SystemEvent 이벤트로 추출하고, 남은 평문은 그대로 반환한다.
 *
 * 정석은 backend에서 `phase_transition` 이벤트를 분리 emit하는 것이지만, 당장
 * 백엔드를 못 건드릴 때 프론트엔드에서 정규식 split로 fallback.
 */

export type PhaseTransition = 'started' | 'completed'

export type PhaseSegment =
  | { kind: 'text'; text: string }
  | { kind: 'event'; phaseId: number; transition: PhaseTransition }

interface PatternMatch {
  start: number
  end: number
  phaseId: number
  transition: PhaseTransition
}

/**
 * Phase narration 패턴 — 우선순위 순으로 적용. 각 정규식은 1번 캡처에 phase id.
 *
 * 완료 패턴:
 *   - `[Phase N 완료]` + 후행 narration (`프로젝트 초기화 완료.` 같은 redundant 한 줄)
 * 시작 패턴:
 *   - `이제 Phase N: <name>을 시작합니다 / 진행하겠습니다`
 *   - `이제 Phase N ... 시작/진행`
 *   - `Phase N: <name>` (단독 헤더)
 */
const COMPLETED_PATTERNS: RegExp[] = [/\[Phase\s+(\d+)\s+완료\][^\n.!?]*[.!?]?\s*/g]

const STARTED_PATTERNS: RegExp[] = [
  /이제\s+Phase\s+(\d+)[^\n.!?]*?(?:시작|진행)(?:하겠습니다|합니다|할게요)?[.!?]?\s*/g,
  /Phase\s+(\d+)\s*[:·][^\n.!?]*[.!?]?\s*/g,
]

function collectMatches(
  text: string,
  patterns: RegExp[],
  transition: PhaseTransition,
): PatternMatch[] {
  const matches: PatternMatch[] = []
  for (const re of patterns) {
    re.lastIndex = 0
    let m: RegExpExecArray | null
    while ((m = re.exec(text)) !== null) {
      const phaseId = Number.parseInt(m[1] ?? '', 10)
      if (!Number.isFinite(phaseId) || phaseId < 1 || phaseId > 8) continue
      matches.push({
        start: m.index,
        end: m.index + m[0].length,
        phaseId,
        transition,
      })
    }
  }
  return matches
}

/**
 * 텍스트를 phase narration / 일반 텍스트 세그먼트로 분리.
 *
 * - 패턴들은 phase 전환 마커로 추출 → SystemEvent로 렌더링.
 * - 매칭 사이의 평문은 trim 후 텍스트 세그먼트로 유지.
 * - 동일 phase + transition이 연속으로 추출되면 dedup.
 */
export function parsePhaseNarration(text: string): PhaseSegment[] {
  if (!text) return []

  const all: PatternMatch[] = [
    ...collectMatches(text, COMPLETED_PATTERNS, 'completed'),
    ...collectMatches(text, STARTED_PATTERNS, 'started'),
  ]
  if (all.length === 0) return [{ kind: 'text', text }]

  // 시작 위치 오름차순. 겹치는 것은 longer 우선 → 앞 패턴 보존
  all.sort((a, b) => a.start - b.start || b.end - a.end)
  const merged: PatternMatch[] = []
  let lastEnd = -1
  for (const m of all) {
    if (m.start >= lastEnd) {
      merged.push(m)
      lastEnd = m.end
    }
  }

  const segments: PhaseSegment[] = []
  let cursor = 0
  let lastEvent: { phaseId: number; transition: PhaseTransition } | null = null
  for (const m of merged) {
    if (m.start > cursor) {
      const slice = text.slice(cursor, m.start).trim()
      if (slice) segments.push({ kind: 'text', text: slice })
    }
    const isDup =
      lastEvent !== null && lastEvent.phaseId === m.phaseId && lastEvent.transition === m.transition
    if (!isDup) {
      segments.push({ kind: 'event', phaseId: m.phaseId, transition: m.transition })
      lastEvent = { phaseId: m.phaseId, transition: m.transition }
    }
    cursor = m.end
  }
  if (cursor < text.length) {
    const slice = text.slice(cursor).trim()
    if (slice) segments.push({ kind: 'text', text: slice })
  }
  return segments
}
