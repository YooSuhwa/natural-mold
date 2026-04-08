'use client'

import Image from 'next/image'
import { BotIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { API_BASE } from '@/lib/api/client'

const sizeMap = {
  xs: { container: 'size-7', icon: 'size-3.5', px: 28 },
  sm: { container: 'size-8', icon: 'size-4', px: 32 },
  md: { container: 'size-10', icon: 'size-5', px: 40 },
  lg: { container: 'size-14', icon: 'size-7', px: 56 },
} as const

interface AgentAvatarProps {
  imageUrl: string | null
  name: string
  size?: keyof typeof sizeMap
  className?: string
}

export function AgentAvatar({ imageUrl, name, size = 'sm', className }: AgentAvatarProps) {
  const { container, icon, px } = sizeMap[size]

  if (imageUrl) {
    return (
      <Image
        src={`${API_BASE}${imageUrl}`}
        alt={name}
        width={px}
        height={px}
        unoptimized
        className={cn('shrink-0 rounded-full object-cover', container, className)}
      />
    )
  }

  return (
    <div
      className={cn(
        'flex shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary',
        container,
        className,
      )}
    >
      <BotIcon className={icon} />
    </div>
  )
}
