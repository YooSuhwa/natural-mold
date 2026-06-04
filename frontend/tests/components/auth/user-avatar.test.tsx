import { render, screen } from '../../test-utils'
import { UserAvatar } from '@/components/auth/UserAvatar'
import type { User } from '@/lib/types/user'

const baseUser: User = {
  id: 'user-1',
  name: 'Real Name',
  email: 'real@example.com',
  is_super_user: false,
  created_at: '2026-05-01T00:00:00Z',
  last_login_at: null,
}

describe('UserAvatar', () => {
  it('uses explicit avatar initials and color when configured', () => {
    render(
      <UserAvatar
        user={{
          ...baseUser,
          display_name: '체스터',
          avatar_mode: 'initials',
          avatar_initials: '췌',
          avatar_color: 'sky',
        }}
      />,
    )

    const avatar = screen.getByLabelText('체스터 프로필 아이콘')
    expect(avatar).toHaveTextContent('췌')
    expect(avatar).toHaveClass('moldy-user-avatar-sky')
  })

  it('falls back to the display name first character', () => {
    render(<UserAvatar user={{ ...baseUser, display_name: '체스터' }} />)

    expect(screen.getByLabelText('체스터 프로필 아이콘')).toHaveTextContent('체')
  })

  it('renders the uploaded image when avatar mode is image', () => {
    render(
      <UserAvatar
        user={{
          ...baseUser,
          display_name: '체스터',
          avatar_mode: 'image',
          avatar_image_url: '/api/auth/me/avatar-image?t=1',
        }}
      />,
    )

    expect(screen.getByRole('img', { name: '체스터 프로필 아이콘' })).toHaveAttribute(
      'src',
      expect.stringContaining('/api/auth/me/avatar-image?t=1'),
    )
  })
})
