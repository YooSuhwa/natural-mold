import { describe, expect, it } from 'vitest'

import {
  findStaticTextIssuesInSource,
  shouldScanPath,
} from '../../../scripts/check-static-i18n.mjs'

describe('static i18n text scanner', () => {
  it('flags JSX text, string props, and toast messages that should come from i18n', () => {
    const source = `
      import { toast } from 'sonner'

      export function Example() {
        const fallbackTitle = '제목 없는 대화'
        toast.error('저장에 실패했어요')
        return (
          <section>
            <h1>모델</h1>
            <input placeholder="검색어 입력" aria-label="검색" />
          </section>
        )
      }
    `

    expect(findStaticTextIssuesInSource(source, 'src/app/example/page.tsx')).toMatchObject([
      { kind: 'string-literal', text: '제목 없는 대화' },
      { kind: 'toast', text: '저장에 실패했어요' },
      { kind: 'jsx-text', text: '모델' },
      { kind: 'jsx-attribute', text: '검색어 입력' },
      { kind: 'jsx-attribute', text: '검색' },
    ])
  })

  it('can audit English UI text in strict ASCII mode', () => {
    const source = `
      import { toast } from 'sonner'

      export function Example() {
        toast.success('Saved successfully')
        return (
          <section>
            <p>Create new agent</p>
            <input aria-label="Search models" />
          </section>
        )
      }
    `

    expect(
      findStaticTextIssuesInSource(source, 'src/app/example/page.tsx', { strictAscii: true }),
    ).toMatchObject([
      { kind: 'toast', text: 'Saved successfully' },
      { kind: 'jsx-text', text: 'Create new agent' },
      { kind: 'jsx-attribute', text: 'Search models' },
    ])
  })

  it('ignores tests, comments, messages, and known domain data files', () => {
    expect(shouldScanPath('src/app/models/page.tsx')).toBe(true)
    expect(shouldScanPath('tests/pages/models.test.tsx')).toBe(false)
    expect(shouldScanPath('src/components/example/example.test.tsx')).toBe(false)
    expect(shouldScanPath('messages/ko.json')).toBe(false)
    expect(shouldScanPath('src/lib/types/index.ts')).toBe(false)
  })

  it('does not flag comments as static UI text', () => {
    const source = `
      // 검색어 입력은 i18n에서 관리한다.
      /*
       * 제목 없는 대화도 마찬가지.
       */
      export const label = t('search.placeholder')
    `

    expect(findStaticTextIssuesInSource(source, 'src/app/example/page.tsx')).toEqual([])
  })
})
