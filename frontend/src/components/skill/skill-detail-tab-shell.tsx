'use client'

import type { ReactNode } from 'react'

import { DialogShell } from '@/components/shared/dialog-shell'

export type SkillDetailTabSlots = {
  readonly body: ReactNode
  readonly bodyClassName?: string
  readonly footer: ReactNode
  readonly overlay?: ReactNode
  readonly sidebar?: ReactNode
  readonly sidebarClassName?: string
}

export type SkillDetailTabRender = (slots: SkillDetailTabSlots) => ReactNode

export function SkillDetailTabShell({ slots }: { readonly slots: SkillDetailTabSlots }) {
  return (
    <>
      {slots.sidebar ? (
        <DialogShell.Split>
          <DialogShell.Sidebar className={slots.sidebarClassName}>
            {slots.sidebar}
          </DialogShell.Sidebar>
          <DialogShell.Body className={slots.bodyClassName}>{slots.body}</DialogShell.Body>
        </DialogShell.Split>
      ) : (
        <DialogShell.Body className={slots.bodyClassName}>{slots.body}</DialogShell.Body>
      )}
      <DialogShell.Footer>{slots.footer}</DialogShell.Footer>
      {slots.overlay ?? null}
    </>
  )
}

export function renderSkillDetailTabShell(slots: SkillDetailTabSlots): ReactNode {
  return <SkillDetailTabShell slots={slots} />
}
