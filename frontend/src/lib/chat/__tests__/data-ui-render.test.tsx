/**
 * End-to-end render proof for the generative UI path A: a ``moldy_ui`` data part
 * (as the converter injects) renders through the registered ``MoldyDataUI`` +
 * allowlist registry, using the REAL assistant-ui external store runtime. This
 * is the unit-level proof of regression-gate item C10.
 */
import { describe, expect, it } from 'vitest'
// test-utils render wraps NextIntl + Query + Tooltip providers (DataTable needs
// useTranslations); demo_note doesn't but data_table does.
import { render, screen, waitFor } from '../../../../tests/test-utils'
import {
  AssistantRuntimeProvider,
  MessagePrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
  type ThreadMessageLike,
} from '@assistant-ui/react'
import { ALL_DATA_UI } from '../data-ui'

interface SourceMessage {
  readonly id: string
  readonly uiType: string
  readonly props: Record<string, unknown>
}

function convertMessage(message: SourceMessage): ThreadMessageLike {
  return {
    role: 'assistant',
    id: message.id,
    content: [
      { type: 'text', text: 'assistant answer' },
      // What the path-A converter injects from message.uiData.
      { type: 'data', name: 'moldy_ui', data: { type: message.uiType, props: message.props } },
    ],
  }
}

function AssistantMessage() {
  // Mirrors assistant-thread.tsx: render data parts via dataRendererUI.
  return (
    <MessagePrimitive.Root>
      <MessagePrimitive.Parts>
        {({ part }) => {
          if (part.type === 'text') return <span>{part.text}</span>
          if (part.type === 'data') return <>{part.dataRendererUI}</>
          return null
        }}
      </MessagePrimitive.Parts>
    </MessagePrimitive.Root>
  )
}

function Harness({ uiType, props }: { uiType: string; props: Record<string, unknown> }) {
  const runtime = useExternalStoreRuntime<SourceMessage>({
    messages: [{ id: 'm1', uiType, props }],
    isRunning: false,
    onNew: async () => {},
    convertMessage,
  })
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root>
        <ThreadPrimitive.Viewport>
          <ThreadPrimitive.Messages components={{ AssistantMessage, UserMessage: () => null }} />
        </ThreadPrimitive.Viewport>
        {ALL_DATA_UI.map((DataComponent, index) => (
          <DataComponent key={index} />
        ))}
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  )
}

describe('generative UI render (path A)', () => {
  it('renders a demo_note data part via the registry', async () => {
    render(<Harness uiType="demo_note" props={{ text: 'PoC works' }} />)
    await waitFor(() =>
      expect(screen.getByTestId('data-ui-demo-note')).toHaveTextContent('PoC works'),
    )
  })

  it('routes a data_table part to the DataTable component', async () => {
    render(
      <Harness
        uiType="data_table"
        props={{
          title: '캡쳐 테이블',
          columns: [{ key: 'name', header: '이름' }],
          rows: [{ name: 'Zed' }],
        }}
      />,
    )
    await waitFor(() => expect(screen.getByTestId('data-ui-data-table')).toBeInTheDocument())
    expect(screen.getByText('캡쳐 테이블')).toBeInTheDocument()
    expect(screen.getByText('Zed')).toBeInTheDocument()
  })

  it('routes a chart part to the Chart component', async () => {
    render(
      <Harness
        uiType="chart"
        props={{ chartType: 'bar', title: '캡쳐 차트', series: [{ label: 'A', value: 5 }] }}
      />,
    )
    await waitFor(() => expect(screen.getByTestId('data-ui-chart')).toBeInTheDocument())
    expect(screen.getByText('캡쳐 차트')).toBeInTheDocument()
  })

  it('routes a stats part to the Stats component', async () => {
    render(
      <Harness uiType="stats" props={{ items: [{ label: '총 요청', value: 1240, delta: 12 }] }} />,
    )
    await waitFor(() => expect(screen.getByTestId('data-ui-stats')).toBeInTheDocument())
    expect(screen.getByText('총 요청')).toBeInTheDocument()
    expect(screen.getByText('1,240')).toBeInTheDocument()
  })

  it('renders nothing for an unknown type (fail-safe, no crash)', async () => {
    render(<Harness uiType="unknown_type" props={{ text: 'x' }} />)
    // The text part still renders; the unknown data part is safely skipped.
    expect(await screen.findByText('assistant answer')).toBeInTheDocument()
    expect(screen.queryByTestId('data-ui-demo-note')).not.toBeInTheDocument()
  })
})
