import { diffLines } from 'diff'

/** 리비전 SKILL.md 라인 diff — 렌더러가 소비하는 평탄화된 라인 목록. */
export type RevisionDiffLine = {
  readonly type: 'added' | 'removed' | 'context'
  readonly text: string
}

export function computeRevisionDiffLines(
  before: string,
  after: string,
): readonly RevisionDiffLine[] {
  const lines: RevisionDiffLine[] = []
  for (const change of diffLines(before, after)) {
    const type = change.added ? 'added' : change.removed ? 'removed' : 'context'
    // diffLines의 chunk는 개행으로 끝난다 — 마지막 빈 조각을 라인으로 세지 않는다.
    const chunk = change.value.endsWith('\n') ? change.value.slice(0, -1) : change.value
    for (const text of chunk.split('\n')) {
      lines.push({ type, text })
    }
  }
  return lines
}

export function hasRevisionDiffChanges(lines: readonly RevisionDiffLine[]): boolean {
  return lines.some((line) => line.type !== 'context')
}
