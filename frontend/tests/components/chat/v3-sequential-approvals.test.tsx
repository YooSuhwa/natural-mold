import { useState } from 'react'
import {
  AuiConfig,
  AssistantRuntimeProvider,
  ThreadPrimitive,
  useExternalMessageConverter,
  useExternalStoreRuntime,
} from '@assistant-ui/react'
import { AIMessage, HumanMessage, ToolMessage } from '@langchain/core/messages'
import type { UseStreamReturn } from '@langchain/react'
import { expect, it } from 'vitest'
import { render, screen, userEvent } from '../../test-utils'
import { AssistantMessageParts } from '@/components/chat/assistant-message-parts'
import { HiTLContext } from '@/lib/chat/hitl-context'
import { resolvedInterruptToolCallsFromDecisions } from '@/lib/chat/langgraph-runtime/hitl-interrupts'
import { useStreamInterruptView } from '@/lib/chat/langgraph-runtime/use-stream-interrupt-view'
import { convertMoldyLangChainMessage } from '@/lib/chat/langgraph-runtime/langchain-message-conversion'
import { ALL_TOOLKIT, createMoldyChatTools } from '@/lib/chat/tool-ui-registry'

function documentTurn(extension: string, skill: string) {
  const args = {
    skill_directory: `/skills/${skill}`,
    command: `node scripts/create_${extension}.cjs --input examples/e2e-${extension}.json --output moldy-${extension}-demo.${extension}`,
  }
  const tool = { id: `call_e2e_${extension}`, name: 'execute_in_skill', args }
  return {
    user: new HumanMessage({ id: `user-${extension}`, content: `E2E_${extension.toUpperCase()}` }),
    call: new AIMessage({ id: `model-${extension}`, content: '', tool_calls: [tool] }),
    result: new ToolMessage({
      id: `result-${extension}`,
      content: 'File created',
      tool_call_id: tool.id,
    }),
    interrupt: {
      id: `interrupt-${extension}`,
      value: {
        action_requests: [{ name: tool.name, args }],
        review_configs: [{ action_name: tool.name, allowed_decisions: ['approve', 'reject'] }],
      },
    },
  }
}

const docx = documentTurn('docx', 'docx-document')
const xlsx = documentTurn('xlsx', 'xlsx-spreadsheet')
const recorded = [
  docx.user,
  docx.call,
  docx.result,
  new AIMessage({ id: 'docx-final', content: 'DOCX is ready' }),
  xlsx.user,
  xlsx.call,
]
const config = AuiConfig({ tools: createMoldyChatTools(ALL_TOOLKIT, 'document-chat') })

function DocumentChat() {
  const [second, setSecond] = useState(false)
  const native = second ? xlsx.interrupt : docx.interrupt
  const { messagesWithInterrupts, payloads, updateResolved } = useStreamInterruptView({
    conversationId: 'document-chat',
    stream: { interrupts: [native] } as unknown as UseStreamReturn<object>,
    serverInterrupts: [],
    messages: second ? recorded : [docx.user, docx.call],
  })
  const converted = useExternalMessageConverter({
    messages: [...messagesWithInterrupts],
    callback: convertMoldyLangChainMessage,
    isRunning: false,
  })
  const runtime = useExternalStoreRuntime({
    messages: converted,
    isRunning: false,
    onNew: async () => {},
  })
  return (
    <AssistantRuntimeProvider runtime={runtime} config={config}>
      <HiTLContext.Provider
        value={{
          onResumeDecisions: async (decisions) => {
            updateResolved((items) => [
              ...items,
              ...resolvedInterruptToolCallsFromDecisions(payloads[0], decisions),
            ])
            setSecond(true)
          },
        }}
      >
        <ThreadPrimitive.Messages>
          {({ message }) => (
            <section>
              {message.role === 'assistant' ? <AssistantMessageParts /> : message.id}
            </section>
          )}
        </ThreadPrimitive.Messages>
      </HiTLContext.Provider>
    </AssistantRuntimeProvider>
  )
}

it('retains the prior document approval in its own turn and renders the next approval as actionable', async () => {
  const user = userEvent.setup()
  render(<DocumentChat />)
  await user.click(await screen.findByRole('button', { name: '승인', exact: true }))
  const secondUser = await screen.findByText('user-xlsx')
  const pending = screen.getByRole('button', { name: '승인', exact: true })
  expect(pending).toBeEnabled()
  const historical = screen.getByText('승인됨')
  expect(
    historical.compareDocumentPosition(secondUser) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy()
  expect(
    secondUser.compareDocumentPosition(pending) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy()
})
