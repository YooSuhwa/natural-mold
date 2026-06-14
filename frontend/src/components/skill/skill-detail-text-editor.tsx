'use client'

import { useState } from 'react'
import { Loader2, Save, Trash2 } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DeleteConfirmInline } from '@/components/shared/delete-confirm-inline'
import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useDeleteSkill, useSkillContent, useUpdateSkillContent } from '@/lib/hooks/use-skills'

import { SkillCredentialBindingsPanel } from './skill-credential-bindings-panel'

export function TextSkillEditor({
  skillId,
  onClose,
  showCredentials = true,
}: {
  readonly skillId: string
  readonly onClose: () => void
  readonly showCredentials?: boolean
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
      onClose()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('deleteFailed'))
    }
  }

  return (
    <>
      <DialogShell.Body>
        {showCredentials ? <SkillCredentialBindingsPanel skillId={skillId} /> : null}
        <Textarea
          value={editor}
          rows={20}
          className="h-full min-h-[400px] font-mono text-xs"
          onChange={(event) => setEditor(event.target.value)}
        />
      </DialogShell.Body>
      <DialogShell.Footer>
        {confirming ? (
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
        )}
        <Button variant="outline" onClick={onClose}>
          {t('close')}
        </Button>
        <Button onClick={handleSave} disabled={update.isPending}>
          {update.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Save className="size-4" />
          )}
          {t('save')}
        </Button>
      </DialogShell.Footer>
    </>
  )
}
