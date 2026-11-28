import { describe, expect, it } from 'vitest'
import { buildCriterion, criterionDraft } from './goal-criterion-form'
import type { GoalCriterion } from '@/lib/types/conversation-goal'

const table: GoalCriterion = {
  kind: 'csv_table',
  logical_path: 'vendors/comparison.csv',
  required_columns: ['Vendor', 'Region', 'Price'],
  minimum_rows: 2,
  unique_by: 'Vendor',
  column_equals: { Region: 'KR' },
  reference_rows: [
    { Vendor: 'A', Region: 'KR', Price: '10' },
    { Vendor: 'B', Region: 'KR', Price: '15' },
  ],
}

describe('goal criterion editing', () => {
  it('round-trips verified reference rows and all column rules through the editor', () => {
    expect(buildCriterion(criterionDraft(table))).toEqual(table)
  })

  it('recognizes normalized filter and key column names in previously stored criteria', () => {
    const draft = criterionDraft({
      ...table,
      column_equals: { REGION: 'KR' },
      unique_by: 'region',
      reference_rows: [],
    })
    expect(buildCriterion(draft)).toMatchObject({
      column_equals: { Region: 'KR' },
      unique_by: 'Region',
    })
  })

  it.each([
    '[{"Vendor":"A","Region":"KR"}]',
    '[{"Vendor":"A","Region":"KR","Price":"10"},{"Vendor":"a","Region":"KR","Price":"11"}]',
    'invalid',
  ])('rejects incomplete or ambiguous reference rows', (references) => {
    expect(buildCriterion({ ...criterionDraft(table), references })).toBeNull()
  })

  it('keeps relative artifact paths and CSV rules valid before saving', () => {
    for (const path of ['/absolute.csv', '../hidden.csv', 'reports/', 'result.pdf']) {
      expect(buildCriterion({ ...criterionDraft(table), path })).toBeNull()
    }
    expect(buildCriterion({ ...criterionDraft(table), minimumRows: '0' })).toBeNull()
    expect(
      buildCriterion(criterionDraft({ kind: 'manual', description: 'x'.repeat(2001) })),
    ).toBeNull()
  })
})
