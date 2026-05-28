import { describe, expect, it } from 'vitest'
import { parsePhaseNarration } from '../builder-phase-parser'

describe('parsePhaseNarration', () => {
  it('returns the original text when no phase markers are present', () => {
    const segs = parsePhaseNarration('안녕하세요! 단계별로 함께 만들어볼게요.')
    expect(segs).toEqual([{ kind: 'text', text: '안녕하세요! 단계별로 함께 만들어볼게요.' }])
  })

  it('extracts a [Phase N 완료] marker and trims redundant narration', () => {
    const segs = parsePhaseNarration('[Phase 1 완료] 프로젝트 초기화 완료.')
    expect(segs).toEqual([{ kind: 'event', phaseId: 1, transition: 'completed' }])
  })

  it('extracts "이제 Phase N: <name>을 시작합니다" as a started event', () => {
    const segs = parsePhaseNarration('이제 Phase 2: 사용자 의도 분석을 시작합니다.')
    expect(segs).toEqual([{ kind: 'event', phaseId: 2, transition: 'started' }])
  })

  it('handles the full mid-session narration from the conversational builder', () => {
    const text =
      '에이전트를 만들어드리겠습니다! 이제 Phase 1: 프로젝트 초기화를 진행하겠습니다.' +
      '[Phase 1 완료] 프로젝트 초기화 완료. 이제 Phase 2: 사용자 의도 분석을 시작합니다.' +
      '이제 사용자 의도를 분석하겠습니다.'

    const segs = parsePhaseNarration(text)
    // 첫 인사 + Phase 1 시작 + Phase 1 완료 + Phase 2 시작 + 나머지 narration
    expect(segs).toEqual([
      { kind: 'text', text: '에이전트를 만들어드리겠습니다!' },
      { kind: 'event', phaseId: 1, transition: 'started' },
      { kind: 'event', phaseId: 1, transition: 'completed' },
      { kind: 'event', phaseId: 2, transition: 'started' },
      { kind: 'text', text: '이제 사용자 의도를 분석하겠습니다.' },
    ])
  })

  it('dedupes consecutive identical events', () => {
    const text = '[Phase 1 완료] [Phase 1 완료]'
    const segs = parsePhaseNarration(text)
    expect(segs).toEqual([{ kind: 'event', phaseId: 1, transition: 'completed' }])
  })

  it('ignores out-of-range phase ids', () => {
    const text = 'Phase 9: foo. Phase 0: bar.'
    const segs = parsePhaseNarration(text)
    expect(segs).toEqual([{ kind: 'text', text }])
  })

  it('returns empty for empty input', () => {
    expect(parsePhaseNarration('')).toEqual([])
  })
})
