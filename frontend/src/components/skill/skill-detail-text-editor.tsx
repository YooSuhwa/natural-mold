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
  const [seeded, setSeeded] = useState<string | undefined>(undefined)

  // 서버 콘텐츠가 바뀌면(예: 버전 탭 롤백 후 stale 캐시 → refetch 착지)
  // 재시드한다 — hydrate-once는 롤백 전 캐시를 잠가 "롤백이 안 먹은" 화면과
  // 저장 시 롤백을 되돌리는 새 리비전을 만든다 (R5). 단 dirty draft는 보호:
  // 사용자가 마지막 시드에서 벗어났다면 편집 내용을 덮지 않는다.
  if (textContent?.content !== undefined && textContent.content !== seeded) {
    const previousSeed = seeded
    setSeeded(textContent.content)
    if (previousSeed === undefined || editor === previousSeed) {
      setEditor(textContent.content)
    }
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
