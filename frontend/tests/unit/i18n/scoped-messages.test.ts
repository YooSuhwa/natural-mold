import { createElement } from 'react'
import { NextIntlClientProvider, useTranslations } from 'next-intl'

import { ROOT_MESSAGE_NAMESPACES, pickMessageNamespaces } from '@/i18n/scoped-messages'
import { render, screen } from '../../test-utils'
import messages from '../../../messages/ko.json'

function RootAssistantTranslationProbe() {
  const assistant = useTranslations('agent.assistant')
  const input = useTranslations('chat.input')
  const message = useTranslations('chat.message')
  const files = useTranslations('chat.files')
  const followup = useTranslations('chat.followup')

  return createElement(
    'div',
    null,
    assistant('title'),
    input('sendButton'),
    message('attach'),
    files('openPanel'),
    followup('toggleOn'),
  )
}

describe('root message scope', () => {
  it('delivers the agent assistant messages used by the retained app-shell panel', () => {
    const messages = {
      agent: { assistant: { title: 'AI Assistant' } },
      chat: { input: { placeholder: 'Ask anything' } },
      common: { close: 'Close' },
      routeOnly: { title: 'Route only' },
    }

    const scoped = pickMessageNamespaces(messages, ROOT_MESSAGE_NAMESPACES)

    expect(ROOT_MESSAGE_NAMESPACES).toContain('agent')
    expect(scoped).toMatchObject({
      agent: { assistant: { title: 'AI Assistant' } },
      chat: { input: { placeholder: 'Ask anything' } },
      common: { close: 'Close' },
    })
    expect(scoped).not.toHaveProperty('routeOnly')
  })

  it('renders the real side-panel translation dependencies from the root scope', () => {
    const scoped = pickMessageNamespaces(messages, ROOT_MESSAGE_NAMESPACES)

    render(
      createElement(
        NextIntlClientProvider,
        { locale: 'ko', messages: scoped },
        createElement(RootAssistantTranslationProbe),
      ),
    )

    expect(screen.getByText(/AI Assistant/)).toBeInTheDocument()
  })
})
