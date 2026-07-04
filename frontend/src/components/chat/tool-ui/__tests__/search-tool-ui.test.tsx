import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SearchRender } from '../search-tool-ui'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}(${Object.values(params).join('/')})` : key,
}))

const DONE = { type: 'complete' } as const

describe('SearchRender', () => {
  it('Tavily answer를 요약 박스로 렌더한다', () => {
    render(
      <SearchRender
        args={{ query: 'agentic os' }}
        result={{
          answer: '에이전틱 OS는 이런 것이다.',
          results: [{ title: '문서', url: 'https://docs.example' }],
        }}
        status={DONE}
      />,
    )
    const box = document.querySelector('[data-moldy-search-answer]')
    expect(box).not.toBeNull()
    expect(screen.getByText('에이전틱 OS는 이런 것이다.')).toBeInTheDocument()
    expect(screen.getByText('문서')).toBeInTheDocument()
  })

  it('Naver items shape를 카드로 렌더한다 (description → 스니펫)', () => {
    render(
      <SearchRender
        args={{ query: '맛집' }}
        result={JSON.stringify({
          http_status: 200,
          total: 2,
          items: [
            { title: '블로그 A', link: 'https://a.example', description: '요약 A' },
            { title: '블로그 B', link: 'https://b.example', description: '요약 B' },
          ],
        })}
        status={DONE}
      />,
    )
    expect(screen.getByText('블로그 A')).toBeInTheDocument()
    expect(screen.getByText('요약 A')).toBeInTheDocument()
    expect(screen.getByText('count(2)')).toBeInTheDocument()
  })

  it('쇼핑 결과는 썸네일과 가격을 렌더한다', () => {
    render(
      <SearchRender
        args={{ query: '키보드' }}
        result={{
          items: [
            {
              title: '기계식 키보드',
              link: 'https://shop.example/1',
              image: 'https://img.example/kb.jpg',
              lprice: '12900',
              mallName: '몰',
            },
          ],
        }}
        status={DONE}
      />,
    )
    const thumbnail = document.querySelector('[data-moldy-search-thumbnail]')
    expect(thumbnail).not.toBeNull()
    expect(thumbnail).toHaveAttribute('src', 'https://img.example/kb.jpg')
    expect(document.querySelector('[data-moldy-search-price]')).not.toBeNull()
    expect(screen.getByText('price(12,900)')).toBeInTheDocument()
    expect(screen.getByText('몰')).toBeInTheDocument()
  })

  it('running 상태에서는 결과/answer를 렌더하지 않는다', () => {
    render(
      <SearchRender
        args={{ query: 'q' }}
        result={{ answer: '미리 온 answer', results: [] }}
        status={{ type: 'running' }}
      />,
    )
    expect(screen.queryByText('미리 온 answer')).not.toBeInTheDocument()
    expect(screen.getByText('running')).toBeInTheDocument()
  })
})
