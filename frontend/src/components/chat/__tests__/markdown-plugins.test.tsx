import Markdown from 'react-markdown'
import { render, screen } from '@testing-library/react'

import { CHAT_STREAMING_REMARK_PLUGINS } from '../markdown-plugins'

const TABLE_MARKDOWN = `| 시간대 | 장소 | 활동 |
|--------|------|------|
| 09:00 ~ 10:30 | 불국사 | 아침 일찍 도착해서 시원하게 유적 탐방 |
`

describe('chat markdown plugins', () => {
  it('parses GFM tables in streaming chat markdown', () => {
    render(<Markdown remarkPlugins={CHAT_STREAMING_REMARK_PLUGINS}>{TABLE_MARKDOWN}</Markdown>)

    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: '시간대' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: '불국사' })).toBeInTheDocument()
  })
})
