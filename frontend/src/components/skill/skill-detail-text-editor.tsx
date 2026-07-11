'use client'

import { useState } from 'react'
import { Loader2, Save, Trash2 } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DeleteConfirmInline } from '@/components/shared/delete-confirm-inline'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useDeleteSkill, useSkillContent, useUpdateSkillContent } from '@/lib/hooks/use-skills'

import { SkillCredentialBindingsPanel } from './skill-credential-bindings-panel'
import type { SkillDetailTabRender } from './skill-detail-tab-shell'

export function TextSkillEditor({
  children,
  skillId,
  onClose,
  showCredentials = true,
  showDangerZone = true,
}: {
  readonly children: SkillDetailTabRender
  readonly skillId: string
  /** 다이얼로그 전용 닫기 — 풀페이지 스튜디오에서는 생략. */
  readonly onClose?: () => void
  readonly showCredentials?: boolean
  /** 스킬 삭제 액션 — 스튜디오에서는 설정 탭이 소유하므로 false. */
  readonly showDangerZone?: boolean
}) {
  const t = useTranslations('skill.detailDialog')
  const { data: textContent } = useSkillContent(skillId, true)
  const update = useUpdateSkillContent()
  const remove = useDeleteSkill()
  const [editor, setEditor] = useState('')
  const [hydrated, setHydrated] = useState(false)
  const [confirming, setConfirming] = useState(false)

  if (!hydrated && textContent?.content !== undefined) {
    setHydrated(true)
    setEditor(textContent.content)
  }

  async function handleSave() {
    try {
      await update.mutateAsync({ id: skillId, data: { content: editor } })
      toast.success(t('saved'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('saveFailed'))
    }
  }

  async function handleDelete() {
    try {
      await remove.mutateAsync(skillId)
      toast.success(t('deleted'))
      onClose?.()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('deleteFailed'))
    }
  }

  return children({
    body: (
      <>
        {showCredentials ? <SkillCredentialBindingsPanel skillId={skillId} /> : null}
        <Textarea
          value={editor}
          rows={20}
          className="h-full min-h-[400px] font-mono text-xs"
          onChange={(event) => setEditor(event.target.value)}
        />
      </>
    ),
    footer: (
      <>
        {showDangerZone ? (
          confirming ? (
            <div className="flex-1">
              <DeleteConfirmInline
                entity={t('skillEntity')}
                onCancel={() => setConfirming(false)}
                onConfirm={handleDelete}
                pending={remove.isPending}
              />
            </div>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              className="mr-auto text-destructive hover:bg-destructive/10 hover:text-destructive"
              onClick={() => setConfirming(true)}
            >
              <Trash2 className="size-3.5" />
              {t('deleteSkill')}
            </Button>
          )
        ) : null}
        {onClose ? (
          <Button variant="outline" onClick={onClose}>
            {t('close')}
          </Button>
        ) : null}
        <Button onClick={handleSave} disabled={update.isPending}>
          {update.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Save className="size-4" />
          )}
          {t('save')}
        </Button>
      </>
    ),
  })
}
