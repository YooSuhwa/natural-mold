'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { FileText, Sparkles, Upload } from 'lucide-react'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { DialogShell } from '@/components/shared/dialog-shell'
import { DomainIconTile } from '@/components/shared/icon'
import { SkillCreateChatTab, SkillCreatePackageTab, SkillCreateTextTab } from './skill-create-tabs'

type TabKey = 'chat' | 'text' | 'package'

interface SkillCreateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  initialTab?: TabKey
  /**
   * Called after successful creation. Receives the new skill id so the caller
   * can immediately open the detail dialog (especially useful for Package /
   * From-scratch flows that produce multi-file skills).
   */
  onCreated?: (skillId: string) => void
  onStartChat?: (request: string) => void
}

function coerceTabKey(value: string): TabKey {
  switch (value) {
    case 'chat':
    case 'text':
    case 'package':
      return value
    default:
      return 'chat'
  }
}

export function SkillCreateDialog({
  open,
  onOpenChange,
  initialTab = 'chat',
  onCreated,
  onStartChat,
}: SkillCreateDialogProps) {
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="lg" height="fixed">
      {open ? (
        <SkillCreateBody
          key={initialTab}
          initialTab={initialTab}
          onClose={() => onOpenChange(false)}
          onCreated={onCreated}
          onStartChat={onStartChat}
        />
      ) : null}
    </DialogShell>
  )
}

function SkillCreateBody({
  initialTab,
  onClose,
  onCreated,
  onStartChat,
}: {
  initialTab: TabKey
  onClose: () => void
  onCreated?: (skillId: string) => void
  onStartChat?: (request: string) => void
}) {
  const t = useTranslations('skill.createDialog')
  const [tab, setTab] = useState<TabKey>(initialTab)

  return (
    <>
      <DialogShell.Header
        icon={<DomainIconTile iconId="skill" className="size-9" iconClassName="size-5" />}
        title={t('title')}
        description={t('description')}
      />
      <DialogShell.Body>
        <Tabs value={tab} onValueChange={(value) => setTab(coerceTabKey(value))}>
          <TabsList variant="line">
            <TabsTrigger value="chat">
              <Sparkles className="size-3.5" /> {t('tabs.chat')}
            </TabsTrigger>
            <TabsTrigger value="text">
              <FileText className="size-3.5" /> {t('tabs.text')}
            </TabsTrigger>
            <TabsTrigger value="package">
              <Upload className="size-3.5" /> {t('tabs.package')}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="chat" className="pt-4">
            <SkillCreateChatTab
              onCancel={onClose}
              onStart={(request) => {
                onStartChat?.(request)
                onClose()
              }}
            />
          </TabsContent>
          <TabsContent value="text" className="pt-4">
            <SkillCreateTextTab onClose={onClose} onCreated={onCreated} />
          </TabsContent>
          <TabsContent value="package" className="pt-4">
            <SkillCreatePackageTab onClose={onClose} onCreated={onCreated} />
          </TabsContent>
        </Tabs>
      </DialogShell.Body>
    </>
  )
}
