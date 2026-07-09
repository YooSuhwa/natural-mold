import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { HiTLContext } from '@/lib/chat/hitl-context'
import { ApprovalCard } from '../approval-card'
import { GroupedApprovalCard } from '../grouped-approval-card'

vi.mock('@assistant-ui/react', () => ({
  makeAssistantToolUI: (config: unknown) => config,
}))

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

type ToolUiRender = {
  render: (props: {
    args: {
      approval_id?: string
      tool_name?: string
      tool_args?: Record<string, unknown>
      description?: string
      message?: string
      hitl_action_index?: number
      hitl_total_actions?: number
      hitl_interrupt_id?: string | null
      allowed_decisions?: string[]
    }
    result?: unknown
    status: { type: string }
    addResult?: (result: unknown) => void
  }) => ReactNode
}

describe('ApprovalCard', () => {
  it('resumes approval even when the runtime cannot accept tool results', async () => {
    const onResumeDecisions = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    const unsupportedAddResult = vi.fn(() => {
      throw new Error('Runtime does not support tool results.')
    })
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          approval_id: 'toolu-1',
          tool_name: 'write_file',
          tool_args: {
            file_path: '/runtime/today_diary.md',
            content: '오늘 하루 기록',
          },
        },
        status: { type: 'requires-action' },
        addResult: unsupportedAddResult,
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    fireEvent.click(screen.getByText('approve'))

    await waitFor(() => {
      expect(onResumeDecisions).toHaveBeenCalledWith([{ type: 'approve' }], 'approved')
    })
    expect(unsupportedAddResult).toHaveBeenCalledTimes(1)
  })

  it('keeps the approval pending when resume fails', async () => {
    const onResumeDecisions = vi.fn<() => Promise<void>>().mockRejectedValue(new Error('stale'))
    const addResult = vi.fn()
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          approval_id: 'toolu-1',
          tool_name: 'write_file',
          tool_args: { file_path: '/runtime/report.md' },
        },
        status: { type: 'requires-action' },
        addResult,
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    fireEvent.click(screen.getByText('approve'))

    expect(await screen.findByText('resumeFailed')).toBeVisible()
    expect(addResult).not.toHaveBeenCalled()
    expect(screen.getByText('approve')).toBeEnabled()
  })

  it('passes the LangGraph interrupt id when registering an approval decision', async () => {
    const registerDecision = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          approval_id: 'interrupt-approval:0',
          tool_name: 'write_file',
          tool_args: { path: 'report.md' },
          hitl_action_index: 0,
          hitl_total_actions: 1,
          hitl_interrupt_id: 'interrupt-approval',
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    fireEvent.click(screen.getByText('approve'))

    await waitFor(() => {
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        { type: 'approve' },
        'approved',
        'interrupt-approval',
      )
    })
  })

  it('keeps a rejected result visible after LangGraph resume accepts the decision', async () => {
    const registerDecision = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          approval_id: 'interrupt-approval:0',
          tool_name: 'execute_in_skill',
          tool_args: { command: 'node scripts/create_docx.cjs' },
          hitl_action_index: 0,
          hitl_total_actions: 1,
          hitl_interrupt_id: 'interrupt-approval',
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    fireEvent.click(screen.getByText('reject'))
    fireEvent.click(screen.getByText('rejectConfirm'))

    await waitFor(() => {
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        { type: 'reject', message: undefined },
        'rejected',
        'interrupt-approval',
      )
    })
    expect(await screen.findByText('rejected')).toBeVisible()
  })

  it('redacts sensitive approval descriptions and args before rendering', () => {
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          approval_id: 'interrupt-approval:0',
          tool_name: 'execute_in_skill',
          description:
            "Args: {'command': 'node scripts/create_docx.cjs', 'api_key': 'raw-secret-value'}",
          tool_args: {
            command: 'node scripts/create_docx.cjs',
            api_key: 'raw-secret-value',
            usage_metadata: { prompt_tokens: 12 },
          },
          allowed_decisions: ['approve', 'edit', 'reject'],
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn() }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    expect(document.body.textContent).not.toContain('raw-secret-value')
    expect(document.body.textContent).toContain('<redacted>')

    fireEvent.click(screen.getByText('args'))
    expect(document.body.textContent).not.toContain('raw-secret-value')
    expect(document.body.textContent).toContain('prompt_tokens')

    fireEvent.click(screen.getByText('edit'))
    expect(document.body.textContent).not.toContain('raw-secret-value')
  })

  it('renders tool args as a readable key/value list, not a raw JSON dump', () => {
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          approval_id: 'toolu-kv',
          tool_name: 'write_file',
          tool_args: { file_path: 'report.md', overwrite: true },
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn() }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    fireEvent.click(screen.getByText('args'))

    // Each arg name is a label; scalar values render plainly (no JSON quotes).
    expect(screen.getByText('file_path')).toBeInTheDocument()
    expect(screen.getByText('report.md')).toBeInTheDocument()
    expect(screen.getByText('overwrite')).toBeInTheDocument()
    expect(screen.getByText('true')).toBeInTheDocument()
    // Not the old raw `JSON.stringify(args, null, 2)` dump.
    expect(document.body.textContent).not.toContain('"file_path"')
    expect(document.body.textContent).not.toContain('{\n')
  })

  it('drops the langchain boilerplate description (header/tool/args duplication)', () => {
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          approval_id: 'toolu-boiler',
          tool_name: 'execute_in_skill',
          tool_args: { command: 'node build.cjs' },
          description:
            'Tool execution requires approval\n\nTool: execute_in_skill\nArgs: {"command": "node build.cjs"}',
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn() }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    // The auto-generated boilerplate (which just repeats the header/tool/args)
    // is suppressed, but the tool name itself is still shown.
    expect(document.body.textContent).not.toContain('Tool execution requires approval')
    expect(document.body.textContent).not.toContain('Args: {')
    expect(screen.getByText('execute_in_skill')).toBeInTheDocument()
  })

  it('names the skill in the headline instead of the generic execute_in_skill', () => {
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          approval_id: 'toolu-skill',
          tool_name: 'execute_in_skill',
          tool_args: {
            skill_directory: '/skills/docx-document',
            command: 'node scripts/create_docx.cjs',
          },
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn() }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    // Headline names the actual skill; the generic mechanism name is gone.
    expect(screen.getByText('docx-document')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('execute_in_skill')
  })

  // Replaces the old "restores redacted placeholders" test, which injected an
  // UN-redacted tool_args and asserted client-side restoration — a path that is
  // a no-op in production (tool_args arrive already redacted) and gave false
  // confidence. The real contract: the secret field is locked read-only and the
  // card submits <redacted>; the BACKEND restores it from the checkpoint by
  // index (covered by test_hitl_wire.py).
  it('locks secret fields read-only and submits <redacted> for the backend to restore', async () => {
    const registerDecision = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          approval_id: 'interrupt-approval:0',
          tool_name: 'write_file',
          // Production shape: tool_args arrive already redacted (key-based).
          tool_args: {
            file_path: '/runtime/report.md',
            api_key: '<redacted>',
          },
          hitl_action_index: 0,
          hitl_total_actions: 1,
          hitl_interrupt_id: 'interrupt-approval',
          allowed_decisions: ['approve', 'edit', 'reject'],
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    fireEvent.click(screen.getByText('edit'))

    // Secret field is locked: disabled and masked as <redacted> — not editable.
    const secretField = screen.getByLabelText('api_key') as HTMLInputElement
    expect(secretField).toBeDisabled()
    expect(secretField).toHaveValue('<redacted>')

    // Only the non-secret field is editable; no raw JSON textarea.
    fireEvent.change(screen.getByLabelText('file_path'), {
      target: { value: '/runtime/updated.md' },
    })
    fireEvent.click(screen.getByText('editAndApprove'))

    await waitFor(() => {
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        {
          type: 'edit',
          edited_action: {
            name: 'write_file',
            args: { file_path: '/runtime/updated.md', api_key: '<redacted>' },
          },
        },
        'editApproved',
        'interrupt-approval',
      )
    })
  })

  it('submits an edit without a tool name (backend fills it by index)', async () => {
    const registerDecision = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: {
          // No tool_name — a merged raw-model-call slot can arrive without it.
          // Edit must still submit (no invalidJson abort); the backend fills
          // edited_action.name by positional index.
          approval_id: 'interrupt-approval:0',
          tool_args: { command: 'node old.cjs' },
          hitl_action_index: 0,
          hitl_total_actions: 1,
          hitl_interrupt_id: 'interrupt-approval',
          allowed_decisions: ['approve', 'edit', 'reject'],
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )

    fireEvent.click(screen.getByText('edit'))
    fireEvent.change(screen.getByLabelText('command'), {
      target: { value: 'node new.cjs' },
    })
    fireEvent.click(screen.getByText('editAndApprove'))

    await waitFor(() => {
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        { type: 'edit', edited_action: { args: { command: 'node new.cjs' } } },
        'editApproved',
        'interrupt-approval',
      )
    })
    expect(screen.queryByText('invalidJson')).toBeNull()
  })

  // ── allowed_decisions 버튼 게이팅 ──────────────────────────────────
  function renderCard(args: Record<string, unknown>, hitl?: Record<string, unknown>) {
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function ApprovalUnderTest() {
      return toolUi.render({
        args: args as never,
        status: { type: 'requires-action' },
      })
    }
    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), ...hitl } as never}>
        <ApprovalUnderTest />
      </HiTLContext.Provider>,
    )
  }

  it('hides the edit button when allowed_decisions excludes edit', () => {
    renderCard({
      approval_id: 'gate-1',
      tool_name: 'execute_in_skill',
      tool_args: { command: 'node build.cjs' },
      allowed_decisions: ['approve', 'reject'],
    })

    expect(screen.getByText('approve')).toBeInTheDocument()
    expect(screen.getByText('reject')).toBeInTheDocument()
    expect(screen.queryByText('edit')).toBeNull()
  })

  it('shows approve, edit, and reject when all are allowed', () => {
    renderCard({
      approval_id: 'gate-2',
      tool_name: 'write_file',
      tool_args: { file_path: 'report.md' },
      allowed_decisions: ['approve', 'edit', 'reject'],
    })

    expect(screen.getByText('approve')).toBeInTheDocument()
    expect(screen.getByText('edit')).toBeInTheDocument()
    expect(screen.getByText('reject')).toBeInTheDocument()
  })

  it('falls back to approve+reject (no edit) when allowed_decisions is missing', () => {
    renderCard({
      approval_id: 'gate-3',
      tool_name: 'write_file',
      tool_args: { file_path: 'report.md' },
    })

    expect(screen.getByText('approve')).toBeInTheDocument()
    expect(screen.getByText('reject')).toBeInTheDocument()
    expect(screen.queryByText('edit')).toBeNull()
  })

  it('shows only reject (with confirm) when allowed_decisions is reject-only', async () => {
    const registerDecision = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    renderCard(
      {
        approval_id: 'gate-4:0',
        tool_name: 'send_email',
        tool_args: { to: 'x@y' },
        hitl_action_index: 0,
        hitl_total_actions: 1,
        hitl_interrupt_id: 'gate-4',
        allowed_decisions: ['reject'],
      },
      { registerDecision },
    )

    expect(screen.queryByText('approve')).toBeNull()
    expect(screen.queryByText('edit')).toBeNull()
    expect(screen.getByText('reject')).toBeInTheDocument()

    fireEvent.click(screen.getByText('reject'))
    fireEvent.click(screen.getByText('rejectConfirm'))

    await waitFor(() => {
      expect(registerDecision).toHaveBeenCalledWith(
        0,
        { type: 'reject', message: undefined },
        'rejected',
        'gate-4',
      )
    })
  })

  // ── 세션 동의 옵션 (스킬 빌더 AD-4) ─────────────────────────────────
  it('세션 동의 옵션은 review_configs 플래그가 있을 때만 렌더된다', () => {
    renderCard({
      approval_id: 'consent-0',
      tool_name: 'test_skill_draft',
      tool_args: { command: 'python scripts/run.py' },
      allowed_decisions: ['approve', 'reject'],
    })
    expect(screen.queryByTestId('approval-session-consent')).toBeNull()
  })

  it('동의 체크 후 승인하면 decision에 scope:session이 첨부된다', async () => {
    const onResumeDecisions = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    renderCard(
      {
        approval_id: 'consent-1',
        tool_name: 'test_skill_draft',
        tool_args: { command: 'python scripts/run.py' },
        allowed_decisions: ['approve', 'reject'],
        session_consent_eligible: true,
      },
      { onResumeDecisions },
    )

    fireEvent.click(screen.getByTestId('approval-session-consent'))
    fireEvent.click(screen.getByText('approve'))

    await waitFor(() => {
      expect(onResumeDecisions).toHaveBeenCalledWith(
        [{ type: 'approve', scope: 'session' }],
        'approved',
      )
    })
  })

  it('동의 체크 없이 승인하면 표준 approve만 전송된다', async () => {
    const onResumeDecisions = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    renderCard(
      {
        approval_id: 'consent-2',
        tool_name: 'test_skill_draft',
        tool_args: { command: 'python scripts/run.py' },
        allowed_decisions: ['approve', 'reject'],
        session_consent_eligible: true,
      },
      { onResumeDecisions },
    )

    expect(screen.getByText('allowForSession')).toBeInTheDocument()
    fireEvent.click(screen.getByText('approve'))

    await waitFor(() => {
      expect(onResumeDecisions).toHaveBeenCalledWith([{ type: 'approve' }], 'approved')
    })
  })

  // ── 멀티액션 그룹 카드 (모두 승인) ──────────────────────────────────
  it('groups multi-action cards: compact rows + one "모두 승인" approves every action', async () => {
    const registerDecision = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function Card({ index }: { index: number }) {
      return toolUi.render({
        args: {
          approval_id: `intr:${index}`,
          tool_name: 'execute_in_skill',
          tool_args: { command: `cmd-${index}` },
          hitl_action_index: index,
          hitl_total_actions: 2,
          hitl_interrupt_id: 'intr',
          allowed_decisions: ['approve', 'reject'],
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <GroupedApprovalCard count={2}>
          <Card index={0} />
          <Card index={1} />
        </GroupedApprovalCard>
      </HiTLContext.Provider>,
    )

    // Compact cards drop their own "승인이 필요합니다" header; the group owns the
    // count header + the single approve-all button.
    expect(screen.queryByText('approvalRequired')).toBeNull()
    expect(screen.getByText('approveAll')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('approval-approve-all-button'))

    await waitFor(() => {
      expect(registerDecision).toHaveBeenCalledWith(0, { type: 'approve' }, 'approved', 'intr')
      expect(registerDecision).toHaveBeenCalledWith(1, { type: 'approve' }, 'approved', 'intr')
    })
  })

  it('does not let "모두 승인" override a card the user put into reject mode', async () => {
    const registerDecision = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    const toolUi = ApprovalCard as unknown as ToolUiRender
    function Card({ index }: { index: number }) {
      return toolUi.render({
        args: {
          approval_id: `intr:${index}`,
          tool_name: 'execute_in_skill',
          tool_args: { command: `cmd-${index}` },
          hitl_action_index: index,
          hitl_total_actions: 2,
          hitl_interrupt_id: 'intr',
          allowed_decisions: ['approve', 'reject'],
        },
        status: { type: 'requires-action' },
      })
    }

    render(
      <HiTLContext.Provider value={{ onResumeDecisions: vi.fn(), registerDecision }}>
        <GroupedApprovalCard count={2}>
          <Card index={0} />
          <Card index={1} />
        </GroupedApprovalCard>
      </HiTLContext.Provider>,
    )

    // Card 0: enter reject mode (reason input open, not yet confirmed).
    fireEvent.click(screen.getAllByText('reject')[0])
    // Approve all.
    fireEvent.click(screen.getByTestId('approval-approve-all-button'))

    await waitFor(() => {
      expect(registerDecision).toHaveBeenCalledWith(1, { type: 'approve' }, 'approved', 'intr')
    })
    // Card 0 (mid-reject) must NOT have been silently approved.
    expect(registerDecision).not.toHaveBeenCalledWith(
      0,
      { type: 'approve' },
      expect.anything(),
      expect.anything(),
    )
  })
})
