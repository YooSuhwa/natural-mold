'use client'

import { SearchIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { ResourceToolbar } from '@/components/shared/resource-layout'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type { InstallationStatus, MarketplaceListFilters } from '@/lib/types/marketplace'

interface MarketplaceFilterBarProps {
  filters: MarketplaceListFilters
  onChange: (next: MarketplaceListFilters) => void
  superUser?: boolean
}

const ALL = '__all__'

export function MarketplaceFilterBar({ filters, onChange, superUser }: MarketplaceFilterBarProps) {
  const t = useTranslations('marketplace.filters')
  const supportOptions = [
    { value: 'one_click', label: t('support.oneClick') },
    { value: 'ready_python', label: t('support.readyPython') },
    { value: 'proxy_http', label: t('support.proxyHttp') },
    { value: 'node_package', label: t('support.nodePackage') },
    { value: 'browser_or_local', label: t('support.browserOrLocal') },
    { value: 'manual_only', label: t('support.manualOnly') },
  ]
  const sourceOptions = [
    { value: 'user', label: t('source.user') },
    { value: 'k-skill', label: t('source.kSkill') },
    { value: 'import', label: t('source.import') },
    { value: 'system_seed', label: t('source.systemSeed') },
  ]
  const installStateOptions: { value: InstallationStatus; label: string }[] = [
    { value: 'active', label: t('installState.active') },
    { value: 'needs_setup', label: t('installState.needsSetup') },
    { value: 'disabled', label: t('installState.disabled') },
  ]
  const sourceLabel =
    sourceOptions.find((opt) => opt.value === filters.source_kind)?.label ?? t('allSources')
  const supportLabel =
    supportOptions.find((opt) => opt.value === filters.support_level)?.label ?? t('allSupport')
  const installStateLabel =
    installStateOptions.find((opt) => opt.value === filters.install_state)?.label ??
    t('allInstallStates')
  const update = (patch: Partial<MarketplaceListFilters>) => {
    onChange({ ...filters, ...patch })
  }

  const reset = () => {
    onChange({ resource_type: filters.resource_type })
  }

  return (
    <ResourceToolbar className="flex-wrap">
      <div className="relative min-w-56 flex-1 sm:max-w-sm">
        <SearchIcon
          className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          value={filters.q ?? ''}
          onChange={(e) => update({ q: e.target.value || undefined })}
          placeholder={t('searchPlaceholder')}
          className="pl-8"
        />
      </div>

      <Select
        value={filters.source_kind ?? ALL}
        onValueChange={(v: string | null) =>
          update({ source_kind: !v || v === ALL ? undefined : v })
        }
      >
        <SelectTrigger className="min-w-36" aria-label={t('sourceFilter')}>
          <SelectValue>{sourceLabel}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>{t('allSources')}</SelectItem>
          {sourceOptions.map((opt) => (
            <SelectItem key={opt.value} value={opt.value}>
              {opt.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={filters.support_level ?? ALL}
        onValueChange={(v: string | null) =>
          update({ support_level: !v || v === ALL ? undefined : v })
        }
      >
        <SelectTrigger className="min-w-40" aria-label={t('supportFilter')}>
          <SelectValue>{supportLabel}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>{t('allSupport')}</SelectItem>
          {supportOptions.map((opt) => (
            <SelectItem key={opt.value} value={opt.value}>
              {opt.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        value={filters.install_state ?? ALL}
        onValueChange={(v: string | null) =>
          update({
            install_state: !v || v === ALL ? undefined : (v as InstallationStatus),
          })
        }
      >
        <SelectTrigger className="min-w-40" aria-label={t('installStateFilter')}>
          <SelectValue>{installStateLabel}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>{t('allInstallStates')}</SelectItem>
          {installStateOptions.map((opt) => (
            <SelectItem key={opt.value} value={opt.value}>
              {opt.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {superUser ? (
        <Button
          variant={filters.is_listed === false ? 'default' : 'outline'}
          size="sm"
          onClick={() =>
            update({
              is_listed: filters.is_listed === false ? undefined : false,
            })
          }
        >
          {t('pendingOnly')}
        </Button>
      ) : null}

      <Button variant="ghost" size="sm" onClick={reset}>
        {t('reset')}
      </Button>
    </ResourceToolbar>
  )
}
