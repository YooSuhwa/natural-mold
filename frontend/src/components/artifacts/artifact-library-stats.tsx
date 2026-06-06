'use client'

import { FileIcon, StarIcon, TimerIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { ArtifactLibraryStats } from '@/lib/types'

interface Props {
  stats?: ArtifactLibraryStats
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function ArtifactLibraryStatsView({ stats }: Props) {
  const t = useTranslations('artifacts.stats')
  const values = [
    { key: 'total', icon: FileIcon, label: t('total'), value: String(stats?.total_count ?? 0) },
    {
      key: 'size',
      icon: FileIcon,
      label: t('size'),
      value: formatBytes(stats?.total_size_bytes ?? 0),
    },
    {
      key: 'favorite',
      icon: StarIcon,
      label: t('favorite'),
      value: String(stats?.favorite_count ?? 0),
    },
    {
      key: 'recent',
      icon: TimerIcon,
      label: t('recent'),
      value: String(stats?.recent_count_7d ?? 0),
    },
  ]
  return (
    <div className="grid gap-3 md:grid-cols-4">
      {values.map((item) => (
        <div key={item.key} className="moldy-card flex items-center gap-3 p-4">
          <item.icon className="size-4 text-muted-foreground" />
          <div>
            <p className="text-xs text-muted-foreground">{item.label}</p>
            <p className="text-lg font-semibold text-foreground">{item.value}</p>
          </div>
        </div>
      ))}
    </div>
  )
}
