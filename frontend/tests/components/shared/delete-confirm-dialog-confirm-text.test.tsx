import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { render, screen, userEvent } from '../../test-utils'

describe('DeleteConfirmDialog "이름 입력" variant (13.12.2)', () => {
  it('enables confirm only on an exact match and hands the typed text back', async () => {
    const onConfirm = vi.fn()
    render(
      <DeleteConfirmDialog
        open
        onOpenChange={vi.fn()}
        title="삭제"
        onConfirm={onConfirm}
        confirmText="gana"
        confirmTextLabel="슬러그 입력"
        confirmLabel="삭제 예약"
      />,
    )
    const confirm = screen.getByRole('button', { name: '삭제 예약' })
    await userEvent.type(screen.getByLabelText('슬러그 입력'), 'Gana')
    expect(confirm).toBeDisabled()
    await userEvent.clear(screen.getByLabelText('슬러그 입력'))
    await userEvent.type(screen.getByLabelText('슬러그 입력'), 'gana')
    await userEvent.click(confirm)
    expect(onConfirm).toHaveBeenCalledWith('gana')
  })

  it('keeps calling onConfirm without arguments for the plain variant', async () => {
    const onConfirm = vi.fn()
    render(<DeleteConfirmDialog open onOpenChange={vi.fn()} title="삭제" onConfirm={onConfirm} />)
    await userEvent.click(screen.getByRole('button', { name: '삭제' }))
    expect(onConfirm).toHaveBeenCalledWith()
  })
})
