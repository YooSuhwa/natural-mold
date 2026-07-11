import { describe, expect, it } from 'vitest'

import {
  deriveSkillStudioContext,
  legacyDetailTabToStudioTab,
  skillStudioTabHref,
} from '../skill-studio-tabs'

const SKILL_ID = '11111111-2222-3333-4444-555555555555'
const SESSION_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'

describe('deriveSkillStudioContext', () => {
  it('목록 라우트는 list 탭', () => {
    expect(deriveSkillStudioContext('/skills')).toEqual({
      activeTab: 'list',
      skillId: null,
      sessionId: null,
    })
  })

  it('빌더 인덱스/세션 라우트는 builder 탭 + sessionId', () => {
    expect(deriveSkillStudioContext('/skills/builder')).toEqual({
      activeTab: 'builder',
      skillId: null,
      sessionId: null,
    })
    expect(deriveSkillStudioContext(`/skills/builder/${SESSION_ID}`)).toEqual({
      activeTab: 'builder',
      skillId: null,
      sessionId: SESSION_ID,
    })
  })

  it('스킬 스코프 탭 세그먼트를 그대로 활성화한다', () => {
    for (const tab of ['evaluation', 'versions', 'source', 'settings'] as const) {
      expect(deriveSkillStudioContext(`/skills/${SKILL_ID}/${tab}`)).toEqual({
        activeTab: tab,
        skillId: SKILL_ID,
        sessionId: null,
      })
    }
  })

  it('탭 세그먼트가 없거나 미지의 값이면 source로 해석 (서버 redirect와 동일)', () => {
    expect(deriveSkillStudioContext(`/skills/${SKILL_ID}`).activeTab).toBe('source')
    expect(deriveSkillStudioContext(`/skills/${SKILL_ID}/unknown`).activeTab).toBe('source')
  })

  it('null/무관 경로는 list로 폴백', () => {
    expect(deriveSkillStudioContext(null).activeTab).toBe('list')
    expect(deriveSkillStudioContext('/agents').activeTab).toBe('list')
  })
})

describe('skillStudioTabHref', () => {
  it('스킬 스코프 탭은 컨텍스트 스킬이 없으면 null(비활성)', () => {
    expect(skillStudioTabHref('evaluation', null)).toBeNull()
    expect(skillStudioTabHref('evaluation', SKILL_ID)).toBe(`/skills/${SKILL_ID}/evaluation`)
  })

  it('빌더 탭은 컨텍스트 스킬을 인덱스 쿼리로 넘긴다', () => {
    expect(skillStudioTabHref('builder', null)).toBe('/skills/builder')
    expect(skillStudioTabHref('builder', SKILL_ID)).toBe(`/skills/builder?skillId=${SKILL_ID}`)
  })

  it('목록 탭은 항상 /skills', () => {
    expect(skillStudioTabHref('list', SKILL_ID)).toBe('/skills')
  })
})

describe('legacyDetailTabToStudioTab', () => {
  it('레거시 다이얼로그 탭 → 스튜디오 세그먼트 매핑 (M2b redirect 계약)', () => {
    expect(legacyDetailTabToStudioTab('content')).toBe('source')
    expect(legacyDetailTabToStudioTab('credentials')).toBe('settings')
    expect(legacyDetailTabToStudioTab('metadata')).toBe('settings')
    expect(legacyDetailTabToStudioTab('evaluation')).toBe('evaluation')
    expect(legacyDetailTabToStudioTab('history')).toBe('versions')
    expect(legacyDetailTabToStudioTab(null)).toBe('source')
    expect(legacyDetailTabToStudioTab('bogus')).toBe('source')
  })
})
