'use client'

import { useTranslations } from 'next-intl'
import {
  WrenchIcon,
  MessageSquareIcon,
  HelpCircleIcon,
  ClockIcon,
  SettingsIcon,
} from 'lucide-react'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { LineTabsList, LineTabsTrigger } from '@/components/ui/line-tabs'
import { AssistantPanel } from '@/components/agent/assistant-panel'
import { TriggersTab } from '../triggers-tab'
import { TestChatPanel } from './test-chat-panel'
import { OpenerEditor } from './opener-editor'
import { SettingsPanel } from './settings-panel'

export type RightTab = 'fix' | 'test' | 'opener' | 'schedule' | 'settings'

interface RightPanelProps {
  tab: RightTab
  onTabChange: (tab: RightTab) => void
  agentId: string
  agentName: string
  agentImageUrl: string | null
  openerQuestions: string[]
  onOpenerQuestionsChange: (q: string[]) => void
  onRequestDeleteTrigger: (target: { id: string; description: string }) => void
  /** 새 에이전트 만들기 모드 — agentId 의존 탭은 placeholder */
  createMode?: boolean
  /** createMode일 때 Fix 첫 메시지 콜백 (createAgent + redirect 부모 처리) */
  onCreateModeFirstMessage?: (msg: string) => Promise<void>
  /** Fix 탭 마운트 시 자동 전송할 초기 메시지 (sessionStorage carry용) */
  initialFixMessage?: string
}

export function RightPanel({
  tab,
  onTabChange,
  agentId,
  agentName,
  agentImageUrl,
  openerQuestions,
  onOpenerQuestionsChange,
  onRequestDeleteTrigger,
  createMode = false,
  onCreateModeFirstMessage,
  initialFixMessage,
}: RightPanelProps) {
  const t = useTranslations('agent.settings')

  return (
    <Tabs
      value={tab}
      onValueChange={(v) => onTabChange(v as RightTab)}
      className="flex min-h-0 flex-1 flex-col"
    >
      <div className="scrollbar-hide sticky top-0 z-10 flex justify-center overflow-x-auto overflow-y-hidden bg-background">
        <LineTabsList>
          <LineTabsTrigger value="fix">
            <WrenchIcon className="size-3.5" />
            {t('tabs.fix')}
          </LineTabsTrigger>
          <LineTabsTrigger value="test">
            <MessageSquareIcon className="size-3.5" />
            {t('tabs.test')}
          </LineTabsTrigger>
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
        </LineTabsList>
      </div>

      <TabsContent
        value="fix"
        keepMounted
        className="flex flex-1 min-h-0 flex-col overflow-hidden p-4 data-[state=inactive]:hidden"
      >
        <AssistantPanel
          agentId={agentId}
          agentName={agentName}
          showHeader={false}
          createMode={createMode}
          onCreateModeFirstMessage={onCreateModeFirstMessage}
          initialMessage={initialFixMessage}
        />
      </TabsContent>

      <TabsContent
        value="test"
        keepMounted
        className="flex flex-1 min-h-0 flex-col overflow-hidden p-4 data-[state=inactive]:hidden"
      >
        {!agentId ? (
          <CreateModePlaceholder label={t('createModeLocked')} />
        ) : (
          <TestChatPanel agentId={agentId} agentName={agentName} agentImageUrl={agentImageUrl} />
        )}
      </TabsContent>

      <TabsContent
        value="opener"
        keepMounted
        className="flex-1 overflow-auto p-4 data-[state=inactive]:hidden"
      >
        <OpenerEditor questions={openerQuestions} onChange={onOpenerQuestionsChange} />
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
        ) : (
          <SettingsPanel agentId={agentId} imageUrl={agentImageUrl} name={agentName} />
        )}
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
