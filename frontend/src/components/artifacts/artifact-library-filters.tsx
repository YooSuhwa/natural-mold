'use client'

import { SearchIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { AgentSummary, ArtifactKind } from '@/lib/types'

const ALL = 'all'
const KINDS: ArtifactKind[] = [
  'image',
  'video',
  'audio',
  'pdf',
  'markdown',
  'html',
  'code',
  'document',
  'data',
  'cad',
  'other',
]

interface Props {
  q: string
  onQChange: (value: string) => void
  agentId: string
  onAgentIdChange: (value: string) => void
  conversationId: string
  onConversationIdChange: (value: string) => void
  kind: string
  onKindChange: (value: string) => void
  favorite: string
  onFavoriteChange: (value: string) => void
  agents?: AgentSummary[]
}

export function ArtifactLibraryFilters({
  q,
  onQChange,
  agentId,
  onAgentIdChange,
  conversationId,
  onConversationIdChange,
  kind,
  onKindChange,
  favorite,
  onFavoriteChange,
  agents,
}: Props) {
  const t = useTranslations('artifacts.filters')
  const tKinds = useTranslations('chat.rightRail.artifacts.kinds')
  return (
    <div className="grid gap-3 md:grid-cols-[minmax(220px,1fr)_160px_180px_150px_150px]">
      <label className="relative">
        <span className="sr-only">{t('search')}</span>
        <SearchIcon className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={q}
          onChange={(event) => onQChange(event.target.value)}
          placeholder={t('searchPlaceholder')}
          className="pl-9"
        />
      </label>
      <Input
        value={conversationId}
        onChange={(event) => onConversationIdChange(event.target.value)}
        placeholder={t('conversationPlaceholder')}
        aria-label={t('conversation')}
      />
      <Select value={agentId} onValueChange={(value) => onAgentIdChange(value ?? ALL)}>
        <SelectTrigger aria-label={t('agent')}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>{t('allAgents')}</SelectItem>
          {(agents ?? []).map((agent) => (
            <SelectItem key={agent.id} value={agent.id}>
              {agent.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select value={kind} onValueChange={(value) => onKindChange(value ?? ALL)}>
        <SelectTrigger aria-label={t('kind')}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>{t('allKinds')}</SelectItem>
          {KINDS.map((item) => (
            <SelectItem key={item} value={item}>
              {tKinds(item)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select value={favorite} onValueChange={(value) => onFavoriteChange(value ?? ALL)}>
        <SelectTrigger aria-label={t('favorite')}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>{t('allFiles')}</SelectItem>
          <SelectItem value="favorite">{t('favoritesOnly')}</SelectItem>
        </SelectContent>
      </Select>
    </div>
  )
}
