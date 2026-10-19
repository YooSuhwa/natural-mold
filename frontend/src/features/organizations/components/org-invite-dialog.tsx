'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'

import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFieldShell } from '@/components/shared/form-field-shell'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useCreateInvitation } from '@/lib/hooks/use-org-governance'
import { useOrgGroups } from '@/lib/hooks/use-organizations'
import type { InvitationRole } from '@/lib/types/org-governance'
import type { DefaultMemberRole } from '@/lib/types/organization'
import { useOrgErrorMessage } from '../lib/error-message'
import { parseInviteEmails } from '../lib/invite-emails'
import { InviteResultList, type InviteResult } from './org-invite-results'
import { OrgSelectField } from './org-select-field'

const ROLES: readonly InvitationRole[] = ['member', 'builder', 'admin', 'auditor']
const NO_GROUP = 'none'

interface OrgInviteDialogProps {
  orgId: string
  defaultRole: DefaultMemberRole
  onClose: () => void
}

/** S11 구성원 초대: several emails, one role and an optional group; 7-day one-time links. */
export function OrgInviteDialog({ orgId, defaultRole, onClose }: OrgInviteDialogProps) {
  const t = useTranslations('orgAdmin.members.invite')
  const tRole = useTranslations('orgAdmin.members.role')
  const tCommon = useTranslations('orgAdmin.common')
  const errorMessage = useOrgErrorMessage()
  const create = useCreateInvitation(orgId)
  const groups = useOrgGroups(orgId)
  const manualGroups = (groups.data?.items ?? []).filter((group) => group.source === 'manual')
  const [text, setText] = useState('')
  const [role, setRole] = useState<InvitationRole>(defaultRole)
  const [groupId, setGroupId] = useState<string>(NO_GROUP)
  const [results, setResults] = useState<InviteResult[] | null>(null)
  const parsed = parseInviteEmails(text)

  async function submit() {
    const collected: InviteResult[] = []
    for (const email of parsed.valid) {
      try {
        const created = await create.mutateAsync({
          email,
          role,
          group_ids: groupId === NO_GROUP ? [] : [groupId],
        })
        collected.push({ email, token: created.token, error: null })
      } catch (error) {
        collected.push({ email, token: null, error: errorMessage(error) })
      }
    }
    setResults(collected)
  }

  return (
    <DialogShell
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="md"
      height="auto"
    >
      <DialogShell.Header title={t('title')} description={t('description')} />
      <DialogShell.Body>
        {results ? (
          <InviteResultList results={results} />
        ) : (
          <div className="space-y-4">
            <FormFieldShell
              id="invite-emails"
              label={t('emails')}
              description={t('emailsHint')}
              error={
                parsed.invalid.length > 0
                  ? t('invalid', { emails: parsed.invalid.join(', ') })
                  : null
              }
              required
            >
              <Textarea
                id="invite-emails"
                value={text}
                onChange={(event) => setText(event.target.value)}
              />
            </FormFieldShell>
            <OrgSelectField
              id="invite-role"
              label={t('role')}
              value={role}
              options={ROLES}
              optionLabel={(value) => tRole(value)}
              onChange={setRole}
            />
            <OrgSelectField
              id="invite-group"
              label={t('group')}
              value={groupId}
              options={[NO_GROUP, ...manualGroups.map((group) => group.id)]}
              optionLabel={(value) =>
                value === NO_GROUP
                  ? t('noGroup')
                  : (manualGroups.find((group) => group.id === value)?.name ?? value)
              }
              onChange={setGroupId}
            />
          </div>
        )}
      </DialogShell.Body>
      <DialogShell.Footer>
        {results ? (
          <Button onClick={onClose}>{tCommon('close')}</Button>
        ) : (
          <>
            <Button variant="outline" onClick={onClose}>
              {tCommon('cancel')}
            </Button>
            <Button
              disabled={parsed.valid.length === 0 || parsed.invalid.length > 0 || create.isPending}
              onClick={() => void submit()}
            >
              {t('submit', { count: parsed.valid.length })}
            </Button>
          </>
        )}
      </DialogShell.Footer>
    </DialogShell>
  )
}
