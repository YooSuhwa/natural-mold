'use client'

import { useCallback, useRef } from 'react'
import { useChannelEffect, type AnyStream, type Event } from '@langchain/react'
import { useSetAtom } from 'jotai'
import {
  setConversationSkillDraftBriefAtom,
  setConversationSkillValidationAtom,
  type SkillDraftBrief,
  type SkillDraftBriefFile,
  type SkillValidationSnapshot,
} from '@/lib/stores/chat-skill-builder'

/**
 * 스킬 빌더 챗 검증 레일 이벤트 소비 (스펙 AD-5).
 *
 * `moldy.skill_draft`(stream-head, stable id `<run_id>:skill_draft`)와
 * `moldy.skill_validation`(도구 결과 projection)을 custom 채널에서 받아
 * conversationId 스코프 스토어에 최신값으로 반영한다.
 * subagent-names-events 계약 미러: `replay: true`(리로드 복원), event_id 기반
 * dedup + 대화 전환 시 seen 리셋.
 */

interface ProtocolCustomEvent {
  readonly method?: string
  readonly event_id?: string
  readonly seq?: number
  readonly params?: {
    readonly data?: unknown
  }
}

interface UseLangGraphSkillBuilderEffectsOptions {
  readonly stream: AnyStream
  readonly conversationId: string
}

const SKILL_BUILDER_CHANNELS = ['custom'] as const

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function textValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function customName(event: ProtocolCustomEvent): string | undefined {
  const method = textValue(event.method)
  if (method?.startsWith('custom:')) return method.slice(7)
  if (method !== 'custom' || !isRecord(event.params?.data)) return undefined
  return textValue(event.params.data.name) ?? textValue(event.params.data.channel)
}

function normalizeCustomName(name: string | undefined): string | undefined {
  if (!name) return undefined
  return name.startsWith('moldy.') ? name.slice('moldy.'.length) : name
}

function payloadCandidate(data: unknown): unknown {
  if (isRecord(data) && isRecord(data.payload)) return data.payload
  return data
}

function parseBriefFiles(value: unknown): SkillDraftBriefFile[] {
  if (!Array.isArray(value)) return []
  const files: SkillDraftBriefFile[] = []
  for (const item of value) {
    if (!isRecord(item)) continue
    const path = textValue(item.path)
    if (!path) continue
    files.push({ path, size: typeof item.size === 'number' ? item.size : 0 })
  }
  return files
}

export function protocolSkillDraftBrief(event: ProtocolCustomEvent): SkillDraftBrief | null {
  if (normalizeCustomName(customName(event)) !== 'skill_draft') return null
  const payload = payloadCandidate(event.params?.data)
  if (!isRecord(payload)) return null
  const sessionId = textValue(payload.session_id)
  if (!sessionId) return null
  return {
    session_id: sessionId,
    mode: textValue(payload.mode) ?? 'create',
    slug: textValue(payload.slug) ?? null,
    file_count: typeof payload.file_count === 'number' ? payload.file_count : 0,
    files: parseBriefFiles(payload.files),
    changed_count: typeof payload.changed_count === 'number' ? payload.changed_count : 0,
    credential_requirement_count:
      typeof payload.credential_requirement_count === 'number'
        ? payload.credential_requirement_count
        : 0,
  }
}

export function protocolSkillValidation(
  event: ProtocolCustomEvent,
): SkillValidationSnapshot | null {
  if (normalizeCustomName(customName(event)) !== 'skill_validation') return null
  const payload = payloadCandidate(event.params?.data)
  if (!isRecord(payload) || !isRecord(payload.validation_result)) return null
  return {
    tool_name: textValue(payload.tool_name) ?? 'validate_skill',
    ...(textValue(payload.session_id) ? { session_id: textValue(payload.session_id) } : {}),
    validation_result: payload.validation_result,
  }
}

export function useLangGraphSkillBuilderEffects({
  stream,
  conversationId,
}: UseLangGraphSkillBuilderEffectsOptions): void {
  const setBrief = useSetAtom(setConversationSkillDraftBriefAtom)
  const setValidation = useSetAtom(setConversationSkillValidationAtom)
  const seenRef = useRef<{ conversationId: string; keys: Set<string> }>({
    conversationId,
    keys: new Set(),
  })

  const handleEvent = useCallback(
    (event: Event) => {
      const brief = protocolSkillDraftBrief(event)
      const validation = brief ? null : protocolSkillValidation(event)
      if (!brief && !validation) return
      if (seenRef.current.conversationId !== conversationId) {
        seenRef.current = { conversationId, keys: new Set() }
      }
      const key = textValue(event.event_id) ?? `skill_builder:${event.seq ?? 'no-seq'}`
      if (seenRef.current.keys.has(key)) return
      seenRef.current.keys.add(key)
      if (brief) setBrief({ conversationId, brief })
      if (validation) setValidation({ conversationId, snapshot: validation })
    },
    [conversationId, setBrief, setValidation],
  )

  useChannelEffect(stream, SKILL_BUILDER_CHANNELS, {
    replay: true,
    bufferSize: 300,
    onEvent: handleEvent,
  })
}
