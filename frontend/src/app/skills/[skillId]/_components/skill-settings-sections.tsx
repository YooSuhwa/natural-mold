'use client'

import { useState, type ReactNode } from 'react'
import { useRouter } from 'next/navigation'
import { Download, Trash2, UploadCloud } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { DeleteConfirmDialog } from '@/components/shared/delete-confirm-dialog'
import { Button } from '@/components/ui/button'
import { PublishWizard } from '@/components/marketplace/publish-wizard'
import { SkillCredentialBindingsPanel } from '@/components/skill/skill-credential-bindings-panel'
import { SkillMetadataTab } from '@/components/skill/skill-metadata-tab'
import type { SkillDetailTabSlots } from '@/components/skill/skill-detail-tab-shell'
import { skillsApi } from '@/lib/api/skills'
import { useDeleteSkill } from '@/lib/hooks/use-skills'
import type { Skill } from '@/lib/types/skill'

/**
 * 설정 탭 (Phase 2 결정 D1) — 목업 5탭이 누락한 자격증명 바인딩·메타데이터
 * 편집을 보존하고, 게시/내보내기/삭제를 에디터 푸터에서 옮겨와 소유한다.
 */
export function SkillSettingsSections({ skill }: { readonly skill: Skill }) {
  const t = useTranslations('skill.studio.settings')
  const dialog = useTranslations('skill.detailDialog')
  const actions = useTranslations('skill.actions')
  const router = useRouter()
  const removeSkill = useDeleteSkill()
  const [publishOpen, setPublishOpen] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  async function handleDelete() {
    try {
      await removeSkill.mutateAsync(skill.id)
      toast.success(dialog('deleted'))
      router.push('/skills')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : dialog('deleteFailed'))
    }
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-8">
      <SettingsSection title={t('metadataTitle')}>
        <SkillMetadataTab skill={skill}>{renderSettingsSectionSlots}</SkillMetadataTab>
      </SettingsSection>

      <SettingsSection title={t('credentialsTitle')}>
        <SkillCredentialBindingsPanel
          skillId={skill.id}
          emptyFallback={
            <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
              {dialog('credentialsEmpty')}
            </div>
          }
        />
      </SettingsSection>

      <SettingsSection title={t('actionsTitle')}>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="outline" onClick={() => setPublishOpen(true)}>
            <UploadCloud className="size-4" />
            {actions('publish')}
          </Button>
          {skill.kind === 'package' ? (
            <Button
              variant="outline"
              render={
                <a
                  href={skillsApi.exportUrl(skill.id)}
                  download
                  aria-label={dialog('exportPackage')}
                />
              }
            >
              <Download className="size-4" />
              {dialog('exportPackage')}
            </Button>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            className="ml-auto text-destructive hover:bg-destructive/10 hover:text-destructive"
            onClick={() => setConfirmingDelete(true)}
          >
            <Trash2 className="size-4" />
            {dialog('deleteSkill')}
          </Button>
        </div>
        <p className="moldy-ui-micro text-muted-foreground">
          {t('deleteHint', { count: skill.used_by_count })}
        </p>
      </SettingsSection>

      <PublishWizard
        skill={publishOpen ? skill : null}
        open={publishOpen}
        onOpenChange={(open) => setPublishOpen(open)}
      />
      <DeleteConfirmDialog
        open={confirmingDelete}
        onOpenChange={(open) => {
          if (!open) setConfirmingDelete(false)
        }}
        title={t('deleteTitle', { name: skill.name })}
        description={t('deleteDescription', { count: skill.used_by_count })}
        confirmLabel={dialog('deleteSkill')}
        isPending={removeSkill.isPending}
        onConfirm={handleDelete}
      />
    </div>
  )
}

function SettingsSection({
  title,
  children,
}: {
  readonly title: string
  readonly children: ReactNode
}) {
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold">{title}</h2>
      {children}
    </section>
  )
}

/** SkillMetadataTab의 4슬롯 출력을 설정 섹션 안에 평면 배치한다. */
function renderSettingsSectionSlots(slots: SkillDetailTabSlots): ReactNode {
  return (
    <>
      {slots.body}
      <div className="flex items-center justify-end gap-2">{slots.footer}</div>
      {slots.overlay ?? null}
    </>
  )
}
