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
  // FixHero와 동일한 크기 (size-44 = 176px, sm:size-52 = 208px). px는 가장 큰 값 기준.
  xl: { container: 'size-44 sm:size-52', icon: 'size-16', px: 208 },
} as const

function appendPreviewVariant(src: string): string {
  if (src.includes('variant=')) return src
  return `${src}${src.includes('?') ? '&' : '?'}variant=preview`
}

interface AgentAvatarProps {
  imageUrl: string | null
  name: string
  size?: keyof typeof sizeMap
  className?: string
  /** true이면 imageUrl을 그대로 사용 (frontend public/* 자산). 기본 false: backend API_BASE prepend */
  publicAsset?: boolean
}

export function AgentAvatar({
  imageUrl,
  name,
  size = 'sm',
  className,
  publicAsset = false,
}: AgentAvatarProps) {
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
    const src = publicAsset ? imageUrl : appendPreviewVariant(`${API_BASE}${imageUrl}`)
    return (
      <Image
        src={src}
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
