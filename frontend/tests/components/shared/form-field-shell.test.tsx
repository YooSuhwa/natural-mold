import { render, screen } from '../../test-utils'
import { Input } from '@/components/ui/input'
import { FormFieldShell } from '@/components/shared/form-field-shell'

describe('FormFieldShell', () => {
  it('associates the label with the field id', () => {
    render(
      <FormFieldShell id="agent-name" label="에이전트 이름">
        <Input id="agent-name" />
      </FormFieldShell>,
    )

    expect(screen.getByLabelText('에이전트 이름')).toBeInTheDocument()
  })

  it('renders description, required mark, and error text', () => {
    render(
      <FormFieldShell
        id="model"
        label="모델"
        description="응답 생성에 사용할 모델입니다."
        required
        error="모델을 선택해 주세요."
      >
        <Input id="model" aria-invalid />
      </FormFieldShell>,
    )

    expect(screen.getByText('*')).toBeInTheDocument()
    expect(screen.getByText('응답 생성에 사용할 모델입니다.')).toHaveAttribute(
      'id',
      'model-description',
    )
    expect(screen.getByText('모델을 선택해 주세요.')).toHaveAttribute('id', 'model-error')
  })

  it('supports inline control layout and field actions', () => {
    render(
      <>
        <FormFieldShell
          id="memory-enabled"
          label="메모리 활성화"
          description="끄면 에이전트가 메모리를 읽지 않습니다."
          layout="inline"
        >
          <Input id="memory-enabled" type="checkbox" />
        </FormFieldShell>
        <FormFieldShell id="model" label="모델" actions={<button type="button">불러오기</button>}>
          <Input id="model" />
        </FormFieldShell>
      </>,
    )

    expect(screen.getByLabelText('메모리 활성화')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '불러오기' })).toBeInTheDocument()
  })
})
