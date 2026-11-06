import { describe, expect, it, vi } from 'vitest'

import { MiddlewaresDialog } from '@/components/agent/visual-settings/dialogs/middlewares-dialog'
import type { MiddlewareRegistryItem } from '@/lib/types'

import { render, screen } from '../../test-utils'

vi.mock('@/lib/hooks/use-middlewares', () => ({ useMiddlewares: () => ({ data: undefined }) }))

const item = (type: string, display: string, extra: Partial<MiddlewareRegistryItem> = {}) => ({
  type,
  name: type,
  display_name: display,
  description: `${display} 설명`,
  category: 'safety',
  config_schema: {},
  provider_specific: null,
  ...extra,
})
const CATALOG: MiddlewareRegistryItem[] = [
  item('pii', 'PII 보호', { superseded_by: 'guardrails' }),
  item('tool_retry', '도구 재시도'),
]

describe('MiddlewaresDialog and superseded types', () => {
  it('does not offer a superseded type for adding', () => {
    render(
      <MiddlewaresDialog
        open
        onOpenChange={() => {}}
        selectedTypes={new Set()}
        onToggleMiddleware={() => {}}
        allMiddlewares={CATALOG}
      />,
    )

    expect(screen.getByText('도구 재시도')).toBeInTheDocument()
    expect(screen.queryByText('PII 보호')).not.toBeInTheDocument()
  })

  it('still shows one an agent already has, with the guardrail note', () => {
    render(
      <MiddlewaresDialog
        open
        onOpenChange={() => {}}
        selectedTypes={new Set(['pii'])}
        onToggleMiddleware={() => {}}
        allMiddlewares={CATALOG}
      />,
    )

    expect(screen.getByText('PII 보호')).toBeInTheDocument()
    expect(screen.getByText(/가드레일로 대체됐습니다/)).toBeInTheDocument()
  })
})
