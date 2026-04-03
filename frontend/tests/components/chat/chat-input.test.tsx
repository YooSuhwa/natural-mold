import { render, screen } from '../../test-utils'
import userEvent from '@testing-library/user-event'
import { ChatInput } from '@/components/chat/chat-input'

describe('ChatInput', () => {
  it('renders textarea and send button', () => {
    render(<ChatInput onSend={vi.fn()} />)
    expect(screen.getByPlaceholderText('메시지 입력...')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '전송' })).toBeInTheDocument()
  })

  it('accepts custom placeholder', () => {
    render(<ChatInput onSend={vi.fn()} placeholder="Type here..." />)
    expect(screen.getByPlaceholderText('Type here...')).toBeInTheDocument()
  })

  it('updates value on input', async () => {
    const user = userEvent.setup()
    render(<ChatInput onSend={vi.fn()} />)
    const textarea = screen.getByPlaceholderText('메시지 입력...')

    await user.type(textarea, 'Hello world')
    expect(textarea).toHaveValue('Hello world')
  })

  it('calls onSend on Enter press', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn()
    render(<ChatInput onSend={onSend} />)
    const textarea = screen.getByPlaceholderText('메시지 입력...')

    await user.type(textarea, 'Hello{Enter}')
    expect(onSend).toHaveBeenCalledWith('Hello')
  })

  it('does not send on Shift+Enter', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn()
    render(<ChatInput onSend={onSend} />)
    const textarea = screen.getByPlaceholderText('메시지 입력...')

    await user.type(textarea, 'Hello{Shift>}{Enter}{/Shift}')
    expect(onSend).not.toHaveBeenCalled()
  })

  it('send button is disabled when input is empty', () => {
    render(<ChatInput onSend={vi.fn()} />)
    expect(screen.getByRole('button', { name: '전송' })).toBeDisabled()
  })

  it('send button is disabled when disabled prop is true', () => {
    render(<ChatInput onSend={vi.fn()} disabled />)
    const textarea = screen.getByPlaceholderText('메시지 입력...')
    expect(textarea).toBeDisabled()
    expect(screen.getByRole('button', { name: '전송' })).toBeDisabled()
  })

  it('trims whitespace before sending', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn()
    render(<ChatInput onSend={onSend} />)
    const textarea = screen.getByPlaceholderText('메시지 입력...')

    await user.type(textarea, '  Hello  {Enter}')
    expect(onSend).toHaveBeenCalledWith('Hello')
  })

  it('clears input after sending', async () => {
    const user = userEvent.setup()
    const onSend = vi.fn()
    render(<ChatInput onSend={onSend} />)
    const textarea = screen.getByPlaceholderText('메시지 입력...')

    await user.type(textarea, 'Hello{Enter}')
    expect(textarea).toHaveValue('')
  })
})
