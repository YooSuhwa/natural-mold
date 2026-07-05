import { describe, expect, it } from 'vitest'
import {
  looksLikeSearchResults,
  parseSearchResults,
  searchAnswerFromResult,
} from '../search-tool-data'

describe('parseSearchResults', () => {
  it('parses Tavily search response objects', () => {
    expect(
      parseSearchResults({
        query: 'agentic os',
        answer: 'summary',
        results: [
          {
            title: 'Spec',
            url: 'https://docs.example/spec',
            content: 'Architecture notes',
            score: 0.9,
            published_date: '2026-06-01',
          },
        ],
        response_time: 1.23,
      }),
    ).toEqual([
      {
        title: 'Spec',
        url: 'https://docs.example/spec',
        content: 'Architecture notes',
        score: 0.9,
        published_date: '2026-06-01',
      },
    ])
  })

  it('parses JSON string Tavily responses', () => {
    expect(
      parseSearchResults(
        '{"results":[{"title":"News","url":"https://news.example","content":"Snippet"}]}',
      ),
    ).toEqual([
      {
        title: 'News',
        url: 'https://news.example',
        content: 'Snippet',
      },
    ])
  })

  it('parses Naver items shape (link/description → url/snippet accessor 대상 필드)', () => {
    expect(
      parseSearchResults({
        http_status: 200,
        total: 1234,
        items: [
          {
            title: '블로그 글',
            link: 'https://blog.naver.example/1',
            description: '본문 요약',
            bloggername: '작성자',
          },
        ],
      }),
    ).toEqual([
      {
        title: '블로그 글',
        link: 'https://blog.naver.example/1',
        description: '본문 요약',
      },
    ])
  })

  it('parses Naver 쇼핑 items (lprice/mallName/image 썸네일)', () => {
    expect(
      parseSearchResults({
        items: [
          {
            title: '기계식 키보드',
            link: 'https://shopping.naver.example/1',
            image: 'https://shopping-phinf.example/img.jpg',
            lprice: '12900',
            mallName: '몰이름',
          },
        ],
      }),
    ).toEqual([
      {
        title: '기계식 키보드',
        link: 'https://shopping.naver.example/1',
        thumbnail: 'https://shopping-phinf.example/img.jpg',
        price: 12900,
        mall_name: '몰이름',
      },
    ])
  })

  it('parses Google 이미지 items (image.thumbnailLink 썸네일)', () => {
    expect(
      parseSearchResults({
        items: [
          {
            title: '이미지',
            link: 'https://images.example/full.jpg',
            image: { thumbnailLink: 'https://images.example/thumb.jpg' },
          },
        ],
      }),
    ).toEqual([
      {
        title: '이미지',
        link: 'https://images.example/full.jpg',
        thumbnail: 'https://images.example/thumb.jpg',
      },
    ])
  })

  it('unwraps MCP text-content 래퍼([{type:text, text:JSON}])', () => {
    expect(
      parseSearchResults([
        {
          type: 'text',
          text: '{"results":[{"title":"MCP","url":"https://mcp.example"}]}',
        },
      ]),
    ).toEqual([{ title: 'MCP', url: 'https://mcp.example' }])
  })
})

describe('searchAnswerFromResult', () => {
  it('Tavily answer 필드를 추출한다 (JSON 문자열 포함)', () => {
    expect(searchAnswerFromResult({ answer: '요약', results: [] })).toBe('요약')
    expect(searchAnswerFromResult('{"answer":"요약","results":[]}')).toBe('요약')
  })

  it('answer가 없거나 공백이면 null', () => {
    expect(searchAnswerFromResult({ results: [] })).toBeNull()
    expect(searchAnswerFromResult({ answer: '  ' })).toBeNull()
    expect(searchAnswerFromResult('plain text')).toBeNull()
  })
})

describe('looksLikeSearchResults', () => {
  it('results|items 배열 + title + url|link면 true', () => {
    expect(looksLikeSearchResults({ results: [{ title: 'A', url: 'https://a.example' }] })).toBe(
      true,
    )
    expect(
      looksLikeSearchResults(
        '{"items":[{"title":"B","link":"https://b.example","description":"d"}]}',
      ),
    ).toBe(true)
  })

  it('title이나 url이 빠지면 false (보수적 판정)', () => {
    expect(looksLikeSearchResults({ items: [{ title: 'no url' }] })).toBe(false)
    expect(looksLikeSearchResults({ results: [{ url: 'https://no-title.example' }] })).toBe(false)
    expect(looksLikeSearchResults({ total: 3 })).toBe(false)
    expect(looksLikeSearchResults('plain text')).toBe(false)
    expect(looksLikeSearchResults(null)).toBe(false)
  })
})

describe('URL sanitize (신뢰 경계 밖 도구 결과)', () => {
  it('javascript: 등 비 http(s) 스킴 링크를 차단한다', async () => {
    const { searchItemUrl, sanitizeExternalUrl } = await import('../search-tool-data')
    expect(searchItemUrl({ url: 'javascript:alert(1)' })).toBeUndefined()
    expect(searchItemUrl({ link: 'data:text/html,<script>1</script>' })).toBeUndefined()
    expect(searchItemUrl({ url: 'https://ok.example/a' })).toBe('https://ok.example/a')
    expect(sanitizeExternalUrl('  http://ok.example ')).toBe('http://ok.example')
  })

  it('썸네일은 http(s) + 로컬 상대경로만 허용한다 (protocol-relative 차단)', async () => {
    const { sanitizeThumbnailUrl } = await import('../search-tool-data')
    expect(sanitizeThumbnailUrl('https://img.example/a.jpg')).toBe('https://img.example/a.jpg')
    expect(sanitizeThumbnailUrl('/logo.webp')).toBe('/logo.webp')
    expect(sanitizeThumbnailUrl('//evil.example/a.jpg')).toBeUndefined()
    expect(sanitizeThumbnailUrl('javascript:alert(1)')).toBeUndefined()
  })
})
