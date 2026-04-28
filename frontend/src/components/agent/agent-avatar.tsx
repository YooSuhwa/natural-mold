'use client'

import { useState } from 'react'
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
  const [hasError, setHasError] = useState(false)
  // imageUrl이 바뀌면 이전 에러 상태 리셋 (React 공식 "rendering 중 비교" 패턴).
  // useEffect + setState는 react-hooks/set-state-in-effect lint를 위반.
  const [prevImageUrl, setPrevImageUrl] = useState(imageUrl)
  if (prevImageUrl !== imageUrl) {
    setPrevImageUrl(imageUrl)
    setHasError(false)
  }

  if (imageUrl && !hasError) {
    return (
      <Image
        src={`${API_BASE}${imageUrl}`}
        alt={name}
        width={px}
        height={px}
        unoptimized
        onError={() => setHasError(true)}
        className={cn(
          'shrink-0 rounded-full bg-emerald-100 object-cover dark:bg-emerald-900',
          container,
          className,
        )}
      />
    )
  }

  return (
    <div
      className={cn(
        'flex shrink-0 items-center justify-center rounded-full bg-emerald-100 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-200',
        container,
        className,
      )}
    >
      <BotIcon className={icon} />
    </div>
  )
}
