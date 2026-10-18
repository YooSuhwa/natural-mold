'use client'

import { useTranslations } from 'next-intl'
import {
  WrenchIcon,
  MessageSquareIcon,
  HelpCircleIcon,
  ClockIcon,
  SettingsIcon,
  KeyRoundIcon,
} from 'lucide-react'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { LineTabsList, LineTabsTrigger } from '@/components/ui/line-tabs'
import { AssistantPanel } from '@/components/agent/assistant-panel'
import { useAgentAccess } from '@/components/agent/agent-access-context'
import type { AgentIdentityMode, RuntimePolicyV1 } from '@/lib/types'
import { TriggersTab } from '../triggers-tab'
import { TestChatPanel } from './test-chat-panel'
import { OpenerEditor } from './opener-editor'
import { SettingsPanel } from './settings-panel'
import { ApiPanel } from './api-panel'

export type RightTab = 'fix' | 'test' | 'opener' | 'schedule' | 'settings' | 'api'

interface RightPanelProps {
  tab: RightTab
  onTabChange: (tab: RightTab) => void
  agentId: string
  agentName: string
  agentImageUrl: string | null
  identityMode?: AgentIdentityMode
  onIdentityModeChange?: (mode: AgentIdentityMode) => void
  /** Existing agents keep this state in the settings-page draft. */
  runtimePolicy?: RuntimePolicyV1 | null
  onRuntimePolicyChange?: (value: RuntimePolicyV1 | null) => void
  modelContextWindow?: number | null
  openerQuestions: string[]
  onOpenerQuestionsChange: (q: string[]) => void
  onRequestDeleteTrigger: (target: { id: string; description: string }) => void
  /** 새 에이전트 만들기 모드 — agentId 의존 탭은 placeholder */
  createMode?: boolean
  onCreateModeFirstMessage?: (msg: string) => Promise<void>
  initialFixMessage?: string
}

export function RightPanel({
  tab,
  onTabChange,
  agentId,
  agentName,
  agentImageUrl,
  identityMode,
  onIdentityModeChange,
  runtimePolicy,
  onRuntimePolicyChange,
  modelContextWindow,
  openerQuestions,
  onOpenerQuestionsChange,
  onRequestDeleteTrigger,
  createMode = false,
  onCreateModeFirstMessage,
  initialFixMessage,
}: RightPanelProps) {
  const t = useTranslations('agent.settings')
  // Plan 13.9 row 2: edit (fix) needs can_edit, the API panel can_deploy. The test chat
  // streams through the assistant endpoint, which also needs can_edit (H-05).
  const { canEdit, canDeploy } = useAgentAccess()

  return (
    <Tabs
      value={tab}
      onValueChange={(v) => onTabChange(v as RightTab)}
      className="flex min-h-0 flex-1 flex-col"
    >
      <div className="scrollbar-hide sticky top-0 z-10 flex justify-center overflow-x-auto overflow-y-hidden bg-background">
        <LineTabsList>
          {canEdit ? (
            <LineTabsTrigger value="fix">
              <WrenchIcon className="size-3.5" />
              {t('tabs.fix')}
            </LineTabsTrigger>
          ) : null}
          {canEdit ? (
            <LineTabsTrigger value="test">
              <MessageSquareIcon className="size-3.5" />
              {t('tabs.test')}
            </LineTabsTrigger>
          ) : null}
          <LineTabsTrigger value="opener">
            <HelpCircleIcon className="size-3.5" />
            {t('tabs.opener')}
          </LineTabsTrigger>
          <LineTabsTrigger value="schedule">
            <ClockIcon className="size-3.5" />
            {t('tabs.schedule')}
          </LineTabsTrigger>
          <LineTabsTrigger value="settings">
            <SettingsIcon className="size-3.5" />
            {t('tabs.settings')}
          </LineTabsTrigger>
          {canDeploy ? (
            <LineTabsTrigger value="api">
              <KeyRoundIcon className="size-3.5" />
              {t('tabs.api')}
            </LineTabsTrigger>
          ) : null}
        </LineTabsList>
      </div>

      <TabsContent
        value="fix"
        keepMounted
        className="flex flex-1 min-h-0 flex-col overflow-hidden p-4 data-[state=inactive]:hidden"
      >
        {canEdit ? (
          <AssistantPanel
            agentId={agentId}
            agentName={agentName}
            showHeader={false}
            createMode={createMode}
            onCreateModeFirstMessage={onCreateModeFirstMessage}
            initialMessage={initialFixMessage}
          />
        ) : null}
      </TabsContent>

      <TabsContent
        value="test"
        keepMounted
        className="flex flex-1 min-h-0 flex-col overflow-hidden p-4 data-[state=inactive]:hidden"
      >
        {!agentId ? (
          <CreateModePlaceholder label={t('createModeLocked')} />
        ) : canEdit ? (
          <TestChatPanel agentId={agentId} agentName={agentName} agentImageUrl={agentImageUrl} />
        ) : null}
      </TabsContent>

      <TabsContent
        value="opener"
        keepMounted
        className="flex-1 overflow-auto p-4 data-[state=inactive]:hidden"
      >
        <fieldset disabled={!canEdit}>
          <OpenerEditor questions={openerQuestions} onChange={onOpenerQuestionsChange} />
        </fieldset>
      </TabsContent>

      <TabsContent
        value="schedule"
        keepMounted
        className="flex-1 overflow-auto p-4 data-[state=inactive]:hidden"
      >
        {!agentId ? (
          <CreateModePlaceholder label={t('createModeLocked')} />
        ) : (
          <TriggersTab agentId={agentId} onRequestDelete={onRequestDeleteTrigger} />
        )}
      </TabsContent>

      <TabsContent
        value="settings"
        keepMounted
        className="flex-1 overflow-auto p-4 data-[state=inactive]:hidden"
      >
        {!agentId ? (
          <CreateModePlaceholder label={t('createModeLocked')} />
        ) : identityMode && onIdentityModeChange ? (
          <fieldset disabled={!canEdit}>
            <SettingsPanel
              agentId={agentId}
              imageUrl={agentImageUrl}
              name={agentName}
              identityMode={identityMode}
              onIdentityModeChange={onIdentityModeChange}
              runtimePolicy={runtimePolicy ?? null}
              onRuntimePolicyChange={onRuntimePolicyChange}
              modelContextWindow={modelContextWindow ?? null}
            />
          </fieldset>
        ) : (
          <CreateModePlaceholder label={t('createModeLocked')} />
        )}
      </TabsContent>

      <TabsContent
        value="api"
        keepMounted
        className="flex-1 overflow-auto p-4 data-[state=inactive]:hidden"
      >
        {!agentId ? (
          <CreateModePlaceholder label={t('createModeLocked')} />
        ) : canDeploy ? (
          <ApiPanel agentId={agentId} agentName={agentName} />
        ) : null}
      </TabsContent>
    </Tabs>
  )
}

function CreateModePlaceholder({ label }: { label: string }) {
  return (
    <div className="moldy-muted-panel flex flex-1 items-center justify-center p-8 text-center">
      <p className="max-w-xs text-sm text-muted-foreground">{label}</p>
    </div>
  )
}
