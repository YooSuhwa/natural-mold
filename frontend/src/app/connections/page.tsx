'use client'

import { useMemo, useState } from 'react'
import { PlusIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { PageHeader } from '@/components/shared/page-header'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useConnections } from '@/lib/hooks/use-connections'
import { ConnectionBindingDialog } from '@/components/connection/connection-binding-dialog'
import { ConnectionCard } from '@/components/connection/connection-card'
import { ConnectionDetailSheet } from '@/components/connection/connection-detail-sheet'
import {
  PREBUILT_PROVIDER_NAMES as PREBUILT_PROVIDERS,
  PREBUILT_PROVIDER_I18N_KEY as PREBUILT_PROVIDER_I18N,
} from '@/lib/types'
import type { Connection, PrebuiltProviderName } from '@/lib/types'

export default function ConnectionsPage() {
  const t = useTranslations('connections')
  const { data: allConnections, isLoading } = useConnections()
  const [detailConnection, setDetailConnection] = useState<Connection | null>(null)

  const grouped = useMemo(() => {
    const acc: Record<'prebuilt' | 'custom' | 'mcp', Connection[]> = {
      prebuilt: [],
      custom: [],
      mcp: [],
    }
    for (const c of allConnections ?? []) acc[c.type].push(c)
    return acc
  }, [allConnections])

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader title={t('pageTitle')} description={t('pageDescription')} />

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      ) : (
        // 빈 상태에서도 3섹션을 항상 노출 — 각 섹션 헤더의 "연결 추가" CTA가
        // first-time user의 유일한 진입점이다.
        <div className="flex flex-col gap-8">
          <PrebuiltSection connections={grouped.prebuilt} onOpenDetail={setDetailConnection} />
          <CustomSection connections={grouped.custom} onOpenDetail={setDetailConnection} />
          <McpSection connections={grouped.mcp} onOpenDetail={setDetailConnection} />
        </div>
      )}

      <ConnectionDetailSheet
        connection={detailConnection}
        onOpenChange={(v) => !v && setDetailConnection(null)}
      />
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────
// PREBUILT Section — provider별 서브그룹
// ─────────────────────────────────────────────────────────────────

function PrebuiltSection({
  connections,
  onOpenDetail,
}: {
  connections: Connection[]
  onOpenDetail: (c: Connection) => void
}) {
  const t = useTranslations('connections.sections.prebuilt')
  const tProvider = useTranslations('connections.providerName')
  const [dialogProvider, setDialogProvider] = useState<PrebuiltProviderName | null>(null)

  const byProvider = useMemo(() => {
    const map = new Map<string, Connection[]>()
    for (const c of connections) {
      const list = map.get(c.provider_name) ?? []
      list.push(c)
      map.set(c.provider_name, list)
    }
    return map
  }, [connections])

  function providerLabel(provider: PrebuiltProviderName): string {
    return tProvider(PREBUILT_PROVIDER_I18N[provider])
  }

  return (
    <section>
      <header className="mb-3">
        <h2 className="text-base font-semibold">{t('title')}</h2>
        <p className="text-sm text-muted-foreground">{t('description')}</p>
      </header>

      <div className="space-y-4">
        {PREBUILT_PROVIDERS.map((provider) => {
          const list = byProvider.get(provider) ?? []
          return (
            <div key={provider} className="rounded-lg border bg-card/50 p-3 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-sm font-medium">{providerLabel(provider)}</h3>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setDialogProvider(provider)}
                >
                  <PlusIcon className="size-3.5" data-icon="inline-start" />
                  {t('addButton')}
                </Button>
              </div>
              {list.length > 0 ? (
                <div className="space-y-2">
                  {list.map((c) => (
                    <ConnectionCard
                      key={c.id}
                      connection={c}
                      onOpenDetail={() => onOpenDetail(c)}
                    />
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">
                  {t('providerEmpty', { provider: providerLabel(provider) })}
                </p>
              )}
            </div>
          )
        })}
      </div>

      {dialogProvider && (
        <ConnectionBindingDialog
          type="prebuilt"
          providerName={dialogProvider}
          toolName={providerLabel(dialogProvider)}
          createNew
          open={!!dialogProvider}
          onOpenChange={(v) => !v && setDialogProvider(null)}
        />
      )}
    </section>
  )
}

// ─────────────────────────────────────────────────────────────────
// CUSTOM Section — flat
// ─────────────────────────────────────────────────────────────────

function CustomSection({
  connections,
  onOpenDetail,
}: {
  connections: Connection[]
  onOpenDetail: (c: Connection) => void
}) {
  const t = useTranslations('connections.sections.custom')
  const [dialogOpen, setDialogOpen] = useState(false)

  return (
    <section>
      <header className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold">{t('title')}</h2>
          <p className="text-sm text-muted-foreground">{t('description')}</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => setDialogOpen(true)}>
          <PlusIcon className="size-3.5" data-icon="inline-start" />
          {t('addButton')}
        </Button>
      </header>

      {connections.length > 0 ? (
        <div className="space-y-2">
          {connections.map((c) => (
            <ConnectionCard key={c.id} connection={c} onOpenDetail={() => onOpenDetail(c)} />
          ))}
        </div>
      ) : (
        <p className="rounded-md border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
          {t('empty')}
        </p>
      )}

      {dialogOpen && (
        <ConnectionBindingDialog
          type="custom"
          open={dialogOpen}
          onOpenChange={setDialogOpen}
        />
      )}
    </section>
  )
}

// ─────────────────────────────────────────────────────────────────
// MCP Section — "연결 추가"는 AddToolDialog MCP 탭으로 위임 (spec §3.3 옵션 A)
// ─────────────────────────────────────────────────────────────────

function McpSection({
  connections,
  onOpenDetail,
}: {
  connections: Connection[]
  onOpenDetail: (c: Connection) => void
}) {
  const t = useTranslations('connections.sections.mcp')
  return (
    <section>
      <header className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold">{t('title')}</h2>
          <p className="text-sm text-muted-foreground">{t('description')}</p>
        </div>
        {/* M6.1 M5 — 신규 MCP 등록 경로는 backend route 재설계 대기 중.
            기존 connection의 credential rotate/ delete는 카드 detail에서 가능. */}
        <Button
          variant="outline"
          size="sm"
          disabled
          title={t('addDisabledHint')}
        >
          <PlusIcon className="size-3.5" data-icon="inline-start" />
          {t('addButton')}
        </Button>
      </header>

      {connections.length > 0 ? (
        <div className="space-y-2">
          {connections.map((c) => (
            <ConnectionCard key={c.id} connection={c} onOpenDetail={() => onOpenDetail(c)} />
          ))}
        </div>
      ) : (
        <p className="rounded-md border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
          {t('empty')}
        </p>
      )}
    </section>
  )
}

