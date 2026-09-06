import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ChatEmptyState } from '../chat-empty-state'

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

vi.mock('@/components/agent/agent-avatar', () => ({
  AgentAvatar: ({ name }: { readonly name: string }) => <span>{name}</span>,
}))

vi.mock('@/lib/hooks/use-templates', () => ({
  useTemplates: () => ({ data: undefined }),
}))

describe('ChatEmptyState assistant-ui v0.15 compatibility', () => {
  it('renders without a runtime provider when the optional composer scope is unavailable', () => {
    render(<ChatEmptyState agent={undefined} fallback="fallback" />)

    expect(screen.getByRole('heading', { name: 'fallback' })).toBeInTheDocument()
  })
})
