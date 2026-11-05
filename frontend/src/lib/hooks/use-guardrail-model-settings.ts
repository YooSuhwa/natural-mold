'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { guardrailModelSettingsApi } from '@/lib/api/guardrail-model-settings'
import { guardrailModelSettingQueryKeys } from '@/lib/query-keys/guardrail-model-settings'
import type {
  GuardrailModelProbeRequest,
  GuardrailModelSettingUpdate,
} from '@/lib/types/guardrail-model-setting'

export function useGuardrailModelSetting() {
  return useQuery({
    queryKey: guardrailModelSettingQueryKeys.all,
    queryFn: guardrailModelSettingsApi.get,
    staleTime: 30_000,
  })
}

export function useUpdateGuardrailModelSetting() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: GuardrailModelSettingUpdate) => guardrailModelSettingsApi.update(data),
    onSuccess: (saved) => qc.setQueryData(guardrailModelSettingQueryKeys.all, saved),
  })
}

export function useResetGuardrailModelSetting() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: guardrailModelSettingsApi.reset,
    onSuccess: () => qc.invalidateQueries({ queryKey: guardrailModelSettingQueryKeys.all }),
  })
}

export function useTestGuardrailModelSetting() {
  return useMutation({
    mutationFn: (data: GuardrailModelProbeRequest) => guardrailModelSettingsApi.test(data),
  })
}
