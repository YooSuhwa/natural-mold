import { describe, expect, it } from 'vitest'
import { parseSearchResults } from '../search-tool-data'

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
})
