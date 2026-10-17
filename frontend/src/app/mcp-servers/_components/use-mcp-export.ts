'use client'

import { toast } from 'sonner'
import { useTranslations } from 'next-intl'

import { useApiErrorMessage } from '@/components/shared/api-error-state'
import { useExportMcpServers } from '@/lib/hooks/use-mcp-servers'

/** Downloads `GET /api/mcp-servers/export` as a JSON file. */
export function useMcpExport() {
  const t = useTranslations('mcp.page')
  const errorMessage = useApiErrorMessage()
  const exportMutation = useExportMcpServers()

  async function handleExport() {
    try {
      const data = await exportMutation.mutateAsync()
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `mcp-servers-${new Date().toISOString().slice(0, 10)}.json`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      const count = Object.keys(data.mcpServers).length
      toast.success(t('toast.exported', { count }))
    } catch (e) {
      toast.error(errorMessage(e, e instanceof Error ? e.message : t('toast.exportFailed')))
    }
  }

  return { handleExport, isPending: exportMutation.isPending }
}
