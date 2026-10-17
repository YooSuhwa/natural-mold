'use client'

import { useTranslations } from 'next-intl'

import { useCredentialAuditLogs } from '@/lib/hooks/use-credentials'
import { formatDisplayDateTime } from '@/lib/utils/display-format'

/** Recent audit rows; only mounted for callers who may edit the credential (13.9). */
export function CredentialAuditSection({ credentialId }: { credentialId: string }) {
  const t = useTranslations('credentials.detail')
  const { data: auditLogs } = useCredentialAuditLogs(credentialId, 10)
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {t('audit.title')}
      </h3>
      {auditLogs && auditLogs.length > 0 ? (
        <ul className="space-y-1.5 text-xs">
          {auditLogs.slice(0, 10).map((log) => (
            <li
              key={log.id}
              className="flex items-start justify-between gap-2 rounded-md border border-border/60 bg-muted/30 px-2 py-1.5"
            >
              <div className="min-w-0">
                <p className="font-medium">{log.action}</p>
                <p className="moldy-ui-caption text-muted-foreground">
                  {log.source}
                  {log.error ? ` · ${log.error}` : ''}
                </p>
              </div>
              <span className="shrink-0 moldy-ui-caption text-muted-foreground">
                {formatDisplayDateTime(log.created_at)}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">{t('audit.empty')}</p>
      )}
    </section>
  )
}
