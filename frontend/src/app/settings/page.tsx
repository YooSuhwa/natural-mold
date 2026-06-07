'use client'

import { useRef, useState, type ChangeEvent, type ReactNode } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useFormatter, useTranslations } from 'next-intl'
import {
  CalendarDaysIcon,
  CheckIcon,
  ClockIcon,
  ImageIcon,
  MailIcon,
  ShieldIcon,
  Trash2Icon,
  UploadIcon,
  UserIcon,
} from 'lucide-react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { authApi, type ProfileUpdatePayload } from '@/lib/api/auth'
import { SESSION_QUERY_KEY, useSession } from '@/lib/auth/session'
import type { User } from '@/lib/types/user'
import { cn } from '@/lib/utils'
import { UserAvatar } from '@/components/auth/UserAvatar'
import { SettingsShell } from './_components/settings-shell'

const AVATAR_COLORS: Array<NonNullable<User['avatar_color']>> = [
  'mint',
  'sky',
  'violet',
  'amber',
  'rose',
  'slate',
]

export default function SettingsPage() {
  const t = useTranslations('appSettings.profile')
  const format = useFormatter()
  const { data: user, isPending } = useSession()

  function formatDate(value?: string | null) {
    if (!value) return t('never')
    return format.dateTime(new Date(value), {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      timeZone: 'Asia/Seoul',
    })
  }

  return (
    <SettingsShell>
      <div className="space-y-4">
        <section className="space-y-1">
          <h2 className="text-lg font-semibold text-foreground">{t('title')}</h2>
          <p className="text-sm leading-6 text-muted-foreground">{t('description')}</p>
        </section>

        {isPending || !user ? (
          <Card>
            <CardContent className="py-6">
              <p className="text-sm text-muted-foreground">{t('loading')}</p>
            </CardContent>
          </Card>
        ) : (
          <ProfileForm
            key={[
              user.id,
              user.display_name ?? '',
              user.avatar_mode ?? 'auto',
              user.avatar_initials ?? '',
              user.avatar_color ?? 'mint',
              user.avatar_image_url ?? '',
            ].join(':')}
            user={user}
            joinedAt={formatDate(user.created_at)}
            lastLoginAt={formatDate(user.last_login_at)}
          />
        )}
      </div>
    </SettingsShell>
  )
}

function ProfileForm({
  user,
  joinedAt,
  lastLoginAt,
}: {
  user: User
  joinedAt: string
  lastLoginAt: string
}) {
  const t = useTranslations('appSettings.profile')
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [displayName, setDisplayName] = useState(user.display_name ?? '')
  const [avatarMode, setAvatarMode] = useState<'auto' | 'initials'>(
    user.avatar_mode === 'auto' ? 'auto' : 'initials',
  )
  const [avatarInitials, setAvatarInitials] = useState(user.avatar_initials ?? '')
  const [avatarColor, setAvatarColor] = useState<NonNullable<User['avatar_color']>>(
    user.avatar_color ?? 'mint',
  )

  const previewUser: User = {
    ...user,
    display_name: displayName.trim() || null,
    avatar_mode: user.avatar_mode === 'image' ? 'image' : avatarMode,
    avatar_initials: avatarInitials.trim() || null,
    avatar_color: avatarColor,
  }

  function cacheUser(next: User) {
    queryClient.setQueryData(SESSION_QUERY_KEY, next)
  }

  const updateProfile = useMutation({
    mutationFn: (payload: ProfileUpdatePayload) => authApi.updateProfile(payload),
    onSuccess: (next) => {
      cacheUser(next)
      toast.success(t('saved'))
    },
  })

  const uploadAvatar = useMutation({
    mutationFn: (file: File) => authApi.uploadAvatarImage(file),
    onSuccess: (next) => {
      cacheUser(next)
      toast.success(t('imageUploaded'))
    },
  })

  const deleteAvatar = useMutation({
    mutationFn: () => authApi.deleteAvatarImage(),
    onSuccess: (next) => {
      cacheUser(next)
      toast.success(t('imageDeleted'))
    },
  })

  const busy = updateProfile.isPending || uploadAvatar.isPending || deleteAvatar.isPending

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    updateProfile.mutate({
      display_name: displayName.trim() || null,
      avatar_mode: avatarMode,
      avatar_initials: avatarInitials.trim() || null,
      avatar_color: avatarColor,
    })
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    uploadAvatar.mutate(file)
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
      <form onSubmit={handleSubmit} className="space-y-4">
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="space-y-1">
                <CardTitle className="flex items-center gap-2">
                  <UserIcon className="size-4" aria-hidden />
                  {t('profileSettings')}
                </CardTitle>
                <CardDescription>{t('profileSettingsDescription')}</CardDescription>
              </div>
              {user.is_super_user ? (
                <Badge variant="secondary" className="bg-status-accent/15 text-status-accent">
                  <ShieldIcon className="size-3" aria-hidden />
                  {t('adminBadge')}
                </Badge>
              ) : null}
            </div>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
              <UserAvatar user={previewUser} size="lg" />
              <div className="min-w-0 space-y-2">
                <p className="text-sm font-medium text-foreground">{t('avatarTitle')}</p>
                <p className="text-sm leading-6 text-muted-foreground">{t('avatarDescription')}</p>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <UploadIcon className="size-4" aria-hidden />
                    {t('uploadImage')}
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={busy || !user.avatar_image_url}
                    onClick={() => deleteAvatar.mutate()}
                  >
                    <Trash2Icon className="size-4" aria-hidden />
                    {t('deleteImage')}
                  </Button>
                </div>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  className="sr-only"
                  onChange={handleFileChange}
                />
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <label htmlFor="settings-display-name" className="text-sm font-medium">
                  {t('displayName')}
                </label>
                <Input
                  id="settings-display-name"
                  value={displayName}
                  maxLength={80}
                  onChange={(event) => setDisplayName(event.target.value)}
                  placeholder={t('displayNamePlaceholder')}
                />
                <p className="text-xs leading-5 text-muted-foreground">{t('displayNameHelp')}</p>
              </div>

              <div className="space-y-2">
                <label htmlFor="settings-avatar-initials" className="text-sm font-medium">
                  {t('avatarInitials')}
                </label>
                <Input
                  id="settings-avatar-initials"
                  value={avatarInitials}
                  maxLength={2}
                  onChange={(event) => setAvatarInitials(event.target.value)}
                  placeholder={t('avatarInitialsPlaceholder')}
                />
                <div className="flex gap-1.5">
                  <AvatarModeButton
                    active={avatarMode === 'auto'}
                    onClick={() => setAvatarMode('auto')}
                  >
                    {t('avatarAuto')}
                  </AvatarModeButton>
                  <AvatarModeButton
                    active={avatarMode === 'initials'}
                    onClick={() => setAvatarMode('initials')}
                  >
                    {t('avatarInitialsMode')}
                  </AvatarModeButton>
                </div>
              </div>
            </div>

            <div className="space-y-2">
              <p className="text-sm font-medium">{t('avatarColor')}</p>
              <div className="flex flex-wrap gap-2">
                {AVATAR_COLORS.map((color) => (
                  <button
                    key={color}
                    type="button"
                    aria-pressed={avatarColor === color}
                    aria-label={t(`colors.${color}`)}
                    onClick={() => setAvatarColor(color)}
                    className={cn(
                      'moldy-user-avatar flex size-8 items-center justify-center rounded-full border text-xs font-semibold',
                      `moldy-user-avatar-${color}`,
                      avatarColor === color &&
                        'ring-2 ring-ring ring-offset-2 ring-offset-background',
                    )}
                  >
                    {avatarColor === color ? <CheckIcon className="size-3.5" aria-hidden /> : null}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex justify-end">
              <Button type="submit" disabled={busy}>
                {t('save')}
              </Button>
            </div>
          </CardContent>
        </Card>
      </form>

      <Card className="h-fit">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <MailIcon className="size-4" aria-hidden />
            {t('accountInfo')}
          </CardTitle>
          <CardDescription>{t('accountInfoDescription')}</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="space-y-3">
            <ProfileField icon={<MailIcon className="size-4" />} label={t('email')}>
              {user.email}
            </ProfileField>
            <ProfileField icon={<CalendarDaysIcon className="size-4" />} label={t('joinedAt')}>
              {joinedAt}
            </ProfileField>
            <ProfileField icon={<ClockIcon className="size-4" />} label={t('lastLoginAt')}>
              {lastLoginAt}
            </ProfileField>
            <ProfileField icon={<ImageIcon className="size-4" />} label={t('avatarModeLabel')}>
              {user.avatar_mode === 'image' ? t('avatarImageMode') : t('avatarLetterMode')}
            </ProfileField>
          </dl>
        </CardContent>
      </Card>
    </div>
  )
}

function AvatarModeButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        'h-7 rounded-md border px-2 text-xs font-medium transition-[background-color,border-color,color]',
        active
          ? 'border-primary bg-primary/10 text-primary-strong'
          : 'border-border text-muted-foreground hover:bg-muted hover:text-foreground',
      )}
    >
      {children}
    </button>
  )
}

function ProfileField({
  icon,
  label,
  children,
}: {
  icon: ReactNode
  label: string
  children: ReactNode
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-muted/30 p-3">
      <dt className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        <span className="text-primary-strong" aria-hidden>
          {icon}
        </span>
        {label}
      </dt>
      <dd className="mt-2 break-words text-sm font-medium text-foreground">{children}</dd>
    </div>
  )
}
