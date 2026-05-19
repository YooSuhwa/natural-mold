'use client'

import { SearchIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type {
  InstallationStatus,
  MarketplaceListFilters,
} from '@/lib/types/marketplace'

interface MarketplaceFilterBarProps {
  filters: MarketplaceListFilters
  onChange: (next: MarketplaceListFilters) => void
  superUser?: boolean
}

const SUPPORT_OPTIONS = [
  { value: 'ready_python', label: 'Python ready' },
  { value: 'proxy_http', label: 'Proxy required' },
  { value: 'node_package', label: 'Node required' },
  { value: 'browser_or_local', label: 'Browser/local' },
  { value: 'manual_only', label: 'Manual only' },
]

const SOURCE_OPTIONS = [
  { value: 'user', label: 'User' },
  { value: 'k-skill', label: 'k-skill' },
  { value: 'import', label: 'Imported' },
  { value: 'system_seed', label: 'System' },
]

const INSTALL_STATE_OPTIONS: { value: InstallationStatus; label: string }[] = [
  { value: 'active', label: 'Active' },
  { value: 'needs_setup', label: 'Needs setup' },
  { value: 'disabled', label: 'Disabled' },
]

const ALL = '__all__'

export function MarketplaceFilterBar({
  filters,
  onChange,
  superUser,
}: MarketplaceFilterBarProps) {
  const update = (patch: Partial<MarketplaceListFilters>) => {
    onChange({ ...filters, ...patch })
  }

  const reset = () => {
    onChange({ resource_type: filters.resource_type })
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative min-w-[200px] flex-1">
        <SearchIcon
          className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
        <Input
          value={filters.q ?? ''}
          onChange={(e) => update({ q: e.target.value || undefined })}
          placeholder="Search marketplace…"
          className="pl-8"
        />
      </div>

      <Select
        value={filters.source_kind ?? ALL}
        onValueChange={(v: string | null) =>
          update({ source_kind: !v || v === ALL ? undefined : v })
        }
      >
        <SelectTrigger className="min-w-[140px]" aria-label="Source filter">
          <SelectValue placeholder="Source" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>All sources</SelectItem>
          {SOURCE_OPTIONS.map((opt) => (
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
        <SelectTrigger className="min-w-[160px]" aria-label="Support level filter">
          <SelectValue placeholder="Support" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>All support</SelectItem>
          {SUPPORT_OPTIONS.map((opt) => (
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
        <SelectTrigger className="min-w-[160px]" aria-label="Install state filter">
          <SelectValue placeholder="Install state" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>All states</SelectItem>
          {INSTALL_STATE_OPTIONS.map((opt) => (
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
          Show pending
        </Button>
      ) : null}

      <Button variant="ghost" size="sm" onClick={reset}>
        Reset
      </Button>
    </div>
  )
}
