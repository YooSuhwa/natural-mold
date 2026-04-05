'use client'

import { useQuery } from '@tanstack/react-query'
import { middlewaresApi } from '@/lib/api/middlewares'

export function useMiddlewares() {
  return useQuery({ queryKey: ['middlewares'], queryFn: middlewaresApi.list })
}
