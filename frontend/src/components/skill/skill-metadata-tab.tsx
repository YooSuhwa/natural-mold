'use client'

import { useState } from 'react'
import { Loader2, Save } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useUpdateSkillMetadata } from '@/lib/hooks/use-skills'
import type { Skill } from '@/lib/types/skill'
import type { SkillDetailTabRender } from './skill-detail-tab-shell'

export function SkillMetadataTab({
  children,
  skill,
  onClose,
}: {
  readonly children: SkillDetailTabRender
  readonly skill: Skill
  readonly onClose: () => void
}) {
  const t = useTranslations('skill.detailDialog')
  const metadata = useTranslations('skill.detailDialog.metadata')
  const update = useUpdateSkillMetadata()
  const [name, setName] = useState(skill.name)
  const [description, setDescription] = useState(skill.description ?? '')
  const [version, setVersion] = useState(skill.version ?? '')
  const canSave =
    name.trim().length > 0 &&
    (name !== skill.name ||
      description !== (skill.description ?? '') ||
      version !== (skill.version ?? ''))

  async function handleSave() {
    try {
      await update.mutateAsync({
        id: skill.id,
        data: {
          name: name.trim(),
          description: description.trim() ? description.trim() : null,
          version: version.trim() ? version.trim() : null,
        },
      })
      toast.success(t('saved'))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('saveFailed'))
    }
  }

  return children({
    body: (
      <>
        <div className="grid gap-4">
          <label className="space-y-1.5">
            <span>{metadata('name')}</span>
            <Input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="space-y-1.5">
            <span>{metadata('description')}</span>
            <Textarea
              value={description}
              rows={4}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>
          <label className="space-y-1.5">
            <span>{metadata('version')}</span>
            <Input value={version} onChange={(event) => setVersion(event.target.value)} />
          </label>
        </div>
      </>
    ),
    footer: (
      <>
        <Button variant="outline" onClick={onClose}>
          {t('close')}
        </Button>
        <Button onClick={handleSave} disabled={!canSave || update.isPending}>
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
