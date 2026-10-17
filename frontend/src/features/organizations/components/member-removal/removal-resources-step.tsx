'use client'

import { useTranslations } from 'next-intl'

import { PrincipalPicker, principalKey } from '@/components/sharing/principal-picker'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  defaultChoice,
  rowKey,
  type ActionableResource,
  type RemovalRow,
  type RowChoice,
} from './removal-plan'

const USER_ONLY = ['user'] as const

interface ResourcesStepProps {
  rows: RemovalRow[]
  leaverId: string
  choices: ReadonlyMap<string, RowChoice>
  onChoose: (key: string, choice: RowChoice) => void
  onApplyOwner: (owner: { id: string; displayName: string }) => void
  /** S17: transfer one resource right away through the E-01 transfer API. */
  onTransferNow: (resource: ActionableResource) => void
}

export function RemovalResourcesStep({
  rows,
  leaverId,
  choices,
  onChoose,
  onApplyOwner,
  onTransferNow,
}: ResourcesStepProps) {
  const t = useTranslations('orgAdmin.memberRemoval.resources')
  const excludeKeys = new Set([principalKey('user', leaverId)])
  const hasTransfers = rows.some((row) => row.kind === 'actionable' && row.canTransfer)

  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">{t('empty')}</p>
  }

  return (
    <div className="space-y-4">
      {hasTransfers ? (
        <PrincipalPicker
          types={USER_ONLY}
          excludeKeys={excludeKeys}
          label={t('applyAll')}
          onPick={(subject) => onApplyOwner({ id: subject.id, displayName: subject.displayName })}
        />
      ) : null}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('resource')}</TableHead>
            <TableHead scope="col">{t('handling')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => {
            const key = rowKey(row.resource)
            return (
              <TableRow key={key}>
                <TableCell className="align-top">
                  <span className="block text-sm">{row.resource.name}</span>
                  <span className="block text-xs text-muted-foreground">
                    {t(`type.${typeKey(row.resource.resource_type)}`)}
                  </span>
                </TableCell>
                <TableCell className="align-top">
                  <RowHandling
                    row={row}
                    choice={choices.get(key) ?? defaultChoice(row)}
                    excludeKeys={excludeKeys}
                    onChoose={(choice) => onChoose(key, choice)}
                    onTransferNow={onTransferNow}
                  />
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

const KNOWN_TYPES = ['agent', 'skill', 'tool', 'mcp_server', 'credential', 'marketplace_item']

function typeKey(type: string): string {
  return KNOWN_TYPES.includes(type) ? type : 'other'
}

function RowHandling({
  row,
  choice,
  excludeKeys,
  onChoose,
  onTransferNow,
}: {
  row: RemovalRow
  choice: RowChoice
  excludeKeys: ReadonlySet<string>
  onChoose: (choice: RowChoice) => void
  onTransferNow: (resource: ActionableResource) => void
}) {
  const t = useTranslations('orgAdmin.memberRemoval.resources')
  const tBlocker = useTranslations('orgAdmin.memberRemoval.blocker')
  const name = row.resource.name

  if (row.kind === 'archived') {
    return <Badge variant="outline">{t('archived')}</Badge>
  }
  if (row.kind === 'unsupported') {
    return <p className="text-xs text-status-danger">{t('unsupported')}</p>
  }
  if (row.kind === 'blocked') {
    return (
      <div className="space-y-1 text-xs text-status-danger">
        <p className="font-medium">{t('blocked')}</p>
        <ul className="list-disc pl-5">
          {row.blockers.map((item) => (
            <li key={item.kind}>{tBlocker(item.kind, { count: item.count })}</li>
          ))}
        </ul>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {row.canTransfer && row.canDelete ? (
        <div className="flex gap-2" role="group" aria-label={t('actionFor', { name })}>
          {(['transfer', 'delete'] as const).map((action) => (
            <Button
              key={action}
              type="button"
              size="sm"
              variant={choice.action === action ? 'secondary' : 'outline'}
              aria-pressed={choice.action === action}
              onClick={() => onChoose({ ...choice, action })}
            >
              {t(`action.${action}`)}
            </Button>
          ))}
        </div>
      ) : null}
      {choice.action === 'delete' ? (
        <p className="text-xs text-muted-foreground">
          {row.canTransfer ? t('deleteNote') : t('personalDelete')}
        </p>
      ) : choice.newOwner ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span>{t('newOwner', { name: choice.newOwner.displayName })}</span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={t('changeOwner', { name })}
            onClick={() => onChoose({ ...choice, newOwner: null })}
          >
            {t('change')}
          </Button>
        </div>
      ) : (
        <PrincipalPicker
          types={USER_ONLY}
          excludeKeys={excludeKeys}
          label={t('ownerFor', { name })}
          onPick={(subject) =>
            onChoose({ ...choice, newOwner: { id: subject.id, displayName: subject.displayName } })
          }
        />
      )}
      {row.canTransfer ? (
        <Button
          type="button"
          variant="link"
          size="sm"
          className="h-auto px-0"
          aria-label={t('transferNowFor', { name })}
          onClick={() => onTransferNow(row.resource)}
        >
          {t('transferNow')}
        </Button>
      ) : null}
    </div>
  )
}
