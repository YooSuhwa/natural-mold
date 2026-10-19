'use client'

import { use, type ReactNode } from 'react'

import { AgentSettingsGate } from '@/components/agent/agent-settings-gate'

export default function AgentSettingsLayout({
  children,
  params,
}: {
  children: ReactNode
  params: Promise<{ agentId: string }>
}) {
  const { agentId } = use(params)
  return <AgentSettingsGate agentId={agentId}>{children}</AgentSettingsGate>
}
