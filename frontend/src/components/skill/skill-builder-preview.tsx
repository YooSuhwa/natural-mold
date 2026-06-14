'use client'

import { useTranslations } from 'next-intl'

import type { SkillBuilderSession, SkillDraftPackage } from '@/lib/types'
import { BuilderResultPanel, ImproveFileSummary } from './skill-builder-preview-insights'

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
      <ImproveFileSummary session={session} draft={draft} />
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
