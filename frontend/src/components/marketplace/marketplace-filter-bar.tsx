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
  { value: 'ready_python', label: 'Python 실행 가능' },
  { value: 'proxy_http', label: '프록시 필요' },
  { value: 'node_package', label: 'Node 필요' },
  { value: 'browser_or_local', label: '브라우저/로컬 필요' },
  { value: 'manual_only', label: '수동 설정' },
]

const SOURCE_OPTIONS = [
  { value: 'user', label: '사용자' },
  { value: 'k-skill', label: 'k-skill' },
  { value: 'import', label: '가져오기' },
  { value: 'system_seed', label: '시스템' },
]

const INSTALL_STATE_OPTIONS: { value: InstallationStatus; label: string }[] = [
  { value: 'active', label: '설치됨' },
  { value: 'needs_setup', label: '설정 필요' },
  { value: 'disabled', label: '비활성화' },
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
          placeholder="마켓플레이스 검색…"
          className="pl-8"
        />
      </div>

      <Select
        value={filters.source_kind ?? ALL}
        onValueChange={(v: string | null) =>
          update({ source_kind: !v || v === ALL ? undefined : v })
        }
      >
        <SelectTrigger className="min-w-[140px]" aria-label="출처 필터">
          <SelectValue placeholder="출처" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>전체 출처</SelectItem>
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
        <SelectTrigger className="min-w-[160px]" aria-label="지원 방식 필터">
          <SelectValue placeholder="지원 방식" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>전체 지원 방식</SelectItem>
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
        <SelectTrigger className="min-w-[160px]" aria-label="설치 상태 필터">
          <SelectValue placeholder="설치 상태" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL}>전체 상태</SelectItem>
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
          대기 항목 보기
        </Button>
      ) : null}

      <Button variant="ghost" size="sm" onClick={reset}>
        초기화
      </Button>
    </div>
  )
}
