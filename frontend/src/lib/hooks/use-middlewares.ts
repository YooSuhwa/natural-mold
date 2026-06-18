'use client'

import { useQuery } from '@tanstack/react-query'
import { middlewaresApi } from '@/lib/api/middlewares'
import { middlewareQueryKeys } from '@/lib/query-keys/middlewares'

export function useMiddlewares() {
  return useQuery({
    queryKey: middlewareQueryKeys.all,
    queryFn: middlewaresApi.list,
    staleTime: 60_000,
  })
}
