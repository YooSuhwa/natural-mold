import { describe, expect, it } from 'vitest'

import { skillsApi } from '../skills'

const API_BASE = 'http://localhost:8001'

describe('skillsApi', () => {
  it('builds a default portable export URL without eval artifacts', () => {
    expect(skillsApi.exportUrl('skill-1')).toBe(`${API_BASE}/api/skills/skill-1/export`)
  })

  it('builds an explicit export URL that includes eval artifacts', () => {
    expect(skillsApi.exportUrl('skill-1', { includeEvals: true })).toBe(
      `${API_BASE}/api/skills/skill-1/export?include_evals=true`,
    )
  })
})
