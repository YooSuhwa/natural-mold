'use client'

import { useTranslations } from 'next-intl'

import { cn } from '@/lib/utils'
import type { SkillBuilderSession, SkillDraftPackage } from '@/lib/types'

interface SkillBuilderPreviewProps {
  readonly session: SkillBuilderSession | null
  readonly draft: SkillDraftPackage | null
}

export function SkillBuilderPreview({ session, draft }: SkillBuilderPreviewProps) {
  const t = useTranslations('skill.builderDialog')
  if (!draft) {
    return (
      <div className="moldy-muted-panel flex min-h-[220px] items-center justify-center p-6 text-center text-sm text-muted-foreground">
        {t('previewEmpty')}
      </div>
    )
  }
  return (
    <>
      <div>
        <p className="text-sm font-semibold">{draft.name}</p>
        <p className="mt-1 text-sm text-muted-foreground">{draft.description}</p>
      </div>
      <FilePreview files={draft.files} />
      <BuilderResultPanel session={session} draft={draft} />
    </>
  )
}

function FilePreview({ files }: { readonly files: SkillDraftPackage['files'] }) {
  const t = useTranslations('skill.builderDialog')
  if (files.length === 0) {
    return <p className="moldy-muted-panel p-3 text-sm text-muted-foreground">{t('filesEmpty')}</p>
  }
  return (
    <div className="space-y-2">
      <p className="text-xs font-medium text-muted-foreground">{t('filesTitle')}</p>
      <ul className="space-y-1">
        {files.map((file) => (
          <li
            key={file.path}
            className="moldy-muted-panel flex items-center justify-between gap-3 px-3 py-2"
          >
            <span className="truncate text-sm font-medium">{file.path}</span>
            <span className="shrink-0 text-xs text-muted-foreground">
              {t(`fileRole.${file.role}`)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function BuilderResultPanel({
  session,
  draft,
}: {
  readonly session: SkillBuilderSession | null
  readonly draft: SkillDraftPackage
}) {
  const t = useTranslations('skill.builderDialog')
  const compatibilityTargets = Object.keys(draft.compatibility_result?.targets ?? {})
  const hasValidation = Boolean(session?.validation_result)
  return (
    <div className="grid gap-2">
      <StatusLine active={hasValidation} label={t('validationTitle')} />
      <StatusLine active={compatibilityTargets.length > 0} label={t('compatibilityTitle')} />
      {compatibilityTargets.length > 0 ? (
        <div className="flex flex-wrap gap-1">
          {compatibilityTargets.map((target) => (
            <span
              key={target}
              className="moldy-status-surface moldy-status-success rounded-md px-2 py-0.5 text-xs"
            >
              {target}
            </span>
          ))}
        </div>
      ) : null}
      <StatusLine active={Boolean(draft.changelog_draft)} label={t('changelogTitle')} />
      <StatusLine
        active={Boolean(draft.benchmark ?? session?.eval_result)}
        label={t('evalTitle')}
      />
    </div>
  )
}

function StatusLine({ active, label }: { readonly active: boolean; readonly label: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <span
        className={cn(
          'inline-block size-2 rounded-sm',
          active ? 'bg-status-success' : 'bg-muted-foreground/30',
        )}
      />
      {label}
    </div>
  )
}
