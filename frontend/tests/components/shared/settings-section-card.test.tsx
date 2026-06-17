import { render, screen } from '../../test-utils'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'

describe('SettingsSectionCard', () => {
  it('renders title, description, actions, and body', () => {
    render(
      <SettingsSectionCard
        title="모델 설정"
        description="기본 모델과 자격증명을 관리합니다."
        actions={<button type="button">저장</button>}
      >
        <div>section body</div>
      </SettingsSectionCard>,
    )

    expect(screen.getByRole('heading', { name: '모델 설정' })).toBeInTheDocument()
    expect(screen.getByText('기본 모델과 자격증명을 관리합니다.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '저장' })).toBeInTheDocument()
    expect(screen.getByText('section body')).toBeInTheDocument()
  })

  it('keeps actions optional', () => {
    render(
      <SettingsSectionCard title="보안">
        <div>body</div>
      </SettingsSectionCard>,
    )

    expect(screen.getByRole('heading', { name: '보안' })).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
