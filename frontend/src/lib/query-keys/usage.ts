import type { UsageDailyParams } from '@/lib/types'

export const usageQueryKeys = {
  summary: (period?: string) => ['usage', 'summary', period] as const,
  daily: (params: UsageDailyParams) => ['usage', 'daily', params] as const,
}
