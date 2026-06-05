'use client'

import Image from 'next/image'
import { useTranslations } from 'next-intl'

import { API_BASE } from '@/lib/api/client'
import { cn } from '@/lib/utils'
import type { User } from '@/lib/types/user'

const sizeMap = {
  xs: { container: 'size-6', px: 24, text: 'moldy-ui-micro' },
  sm: { container: 'size-8', px: 32, text: 'text-xs' },
  md: { container: 'size-10', px: 40, text: 'text-sm' },
  lg: { container: 'size-16', px: 64, text: 'text-xl' },
} as const

export type UserAvatarSize = keyof typeof sizeMap

export function displayUserName(user: User | null | undefined): string {
  if (!user) return ''
  return user.display_name?.trim() || user.name?.trim() || user.email
}

function firstGrapheme(value: string): string {
  return Array.from(value.trim())[0]?.toUpperCase() || '?'
}

function avatarText(user: User | null | undefined): string {
  if (!user) return '?'
  if (user.avatar_mode === 'initials' && user.avatar_initials?.trim()) {
    return user.avatar_initials.trim().slice(0, 2).toUpperCase()
  }
  return firstGrapheme(displayUserName(user) || user.email)
}

function avatarImageSrc(user: User | null | undefined): string | null {
  if (user?.avatar_mode !== 'image' || !user.avatar_image_url) return null
  if (user.avatar_image_url.startsWith('http')) return user.avatar_image_url
  return `${API_BASE}${user.avatar_image_url}`
}

interface UserAvatarProps {
  user: User | null | undefined
  size?: UserAvatarSize
  className?: string
}

export function UserAvatar({ user, size = 'sm', className }: UserAvatarProps) {
  const t = useTranslations('auth.userMenu')
  const { container, px, text } = sizeMap[size]
  const label = t('avatarLabel', {
    name: displayUserName(user) || user?.email || t('avatarFallback'),
  })
  const imageSrc = avatarImageSrc(user)

  if (imageSrc) {
    return (
      <Image
        src={imageSrc}
        alt={label}
        width={px}
        height={px}
        unoptimized
        className={cn('shrink-0 rounded-full object-cover', container, className)}
      />
    )
  }

  const color = user?.avatar_color ?? 'mint'
  return (
    <div
      role="img"
      aria-label={label}
      className={cn(
        'moldy-user-avatar flex shrink-0 items-center justify-center rounded-full font-semibold',
        `moldy-user-avatar-${color}`,
        container,
        text,
        className,
      )}
    >
      {avatarText(user)}
    </div>
  )
}
