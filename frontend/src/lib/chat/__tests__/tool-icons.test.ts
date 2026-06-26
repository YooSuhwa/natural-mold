import { describe, expect, it } from 'vitest'
import { ClockIcon, SearchIcon, WrenchIcon } from 'lucide-react'
import { toolIcon } from '../tool-icons'
import { toolCallChildLabel } from '../tool-group-meta'

describe('toolIcon', () => {
  it('알려진 빌트인 도구는 의미 아이콘', () => {
    expect(toolIcon('current_datetime')).toBe(ClockIcon)
    expect(toolIcon('web_search')).toBe(SearchIcon)
  })

  it('접두사 매핑(naver_/google_)', () => {
    expect(toolIcon('naver_blog_search')).toBe(SearchIcon)
    expect(toolIcon('google_news_search')).toBe(SearchIcon)
  })

  it('모르는 도구는 렌치 폴백', () => {
    expect(toolIcon('some_random_mcp_tool')).toBe(WrenchIcon)
  })
})

describe('toolCallChildLabel', () => {
  it('대표 인자(query/file_path)를 우선', () => {
    expect(toolCallChildLabel({ query: 'react 19' }, undefined)).toBe('react 19')
    expect(toolCallChildLabel({ file_path: 'src/app/page.tsx' }, undefined)).toBe('src/app/page.tsx')
  })

  it('대표 인자가 없으면 첫 문자열 인자', () => {
    expect(toolCallChildLabel({ foo: 'bar baz' }, undefined)).toBe('bar baz')
  })

  it('인자가 없으면 결과 미리보기(첫 줄)', () => {
    expect(toolCallChildLabel({}, '2026-06-27 14:03\n추가 내용')).toBe('2026-06-27 14:03')
  })

  it('JSON 결과는 raw가 아니라 대표 스칼라 값만', () => {
    expect(toolCallChildLabel({}, '{"now_iso": "2026-06-27T06:41:51+09:00", "tz": "KST"}')).toBe(
      '2026-06-27T06:41:51+09:00',
    )
    expect(toolCallChildLabel({}, '{"results":[{"title":"hello","url":"x"}]}')).toBe('hello')
  })

  it('아무것도 없으면 null(호출 측이 도구명 폴백)', () => {
    expect(toolCallChildLabel({}, undefined)).toBeNull()
    expect(toolCallChildLabel({ n: 5 }, { obj: true })).toBeNull()
  })

  it('긴 값은 말줄임', () => {
    const long = 'a'.repeat(80)
    const out = toolCallChildLabel({ query: long }, undefined) ?? ''
    expect(out.length).toBeLessThanOrEqual(48)
    expect(out.endsWith('…')).toBe(true)
  })
})
