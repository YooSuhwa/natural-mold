"use client"

import { useQuery } from "@tanstack/react-query"
import { usageApi } from "@/lib/api/usage"

export function useUsageSummary(period?: string) {
  return useQuery({
    queryKey: ["usage", period],
    queryFn: () => usageApi.summary(period),
    staleTime: 60000,
  })
}
