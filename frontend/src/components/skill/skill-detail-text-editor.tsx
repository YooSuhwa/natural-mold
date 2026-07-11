'use client'

import { useState } from 'react'
import { Loader2, Save } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useSkillContent, useUpdateSkillContent } from '@/lib/hooks/use-skills'

import type { SkillDetailTabRender } from './skill-detail-tab-shell'

/**
 * 소스 탭(텍스트) 에디터 — 저장(=리비전)만 소유한다. 삭제/자격증명은
 * 스튜디오 설정 탭 소관 (Phase 2 D1).
 */
export function TextSkillEditor({
  children,
  skillId,
}: {
  readonly children: SkillDetailTabRender
  readonly skillId: string
}) {
  const t = useTranslations('skill.detailDialog')
  const { data: textContent } = useSkillContent(skillId, true)
  const update = useUpdateSkillContent()
  const [editor, setEditor] = useState('')
  const [hydrated, setHydrated] = useState(false)

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

  return children({
    body: (
      <>
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
