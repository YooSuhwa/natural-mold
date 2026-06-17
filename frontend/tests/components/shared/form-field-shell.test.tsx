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
})
