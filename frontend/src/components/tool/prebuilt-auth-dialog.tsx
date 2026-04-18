'use client'

import React, { useState } from 'react'
import { useTranslations } from 'next-intl'

import { ConnectionBindingDialog } from '@/components/connection/connection-binding-dialog'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import type { Tool } from '@/lib/types'

interface PrebuiltAuthDialogProps {
  tool: Tool
  trigger: React.ReactNode
}

/**
 * PREBUILT 도구 인증 dialog — ConnectionBindingDialog 래퍼.
 *
 * ADR-008: PREBUILT는 per-user Connection 엔티티(user_id+type+provider_name)로
 * credential을 바인딩한다. tool row 자체는 공유 행이라 mutate하지 않는다.
 *
 * tool.provider_name이 null이면 legacy seed(m10 매핑 실패)라 connection 경로를
 * 쓸 수 없다 → trigger disabled + 안내 tooltip. 실무상 0건 예상.
 */
export function PrebuiltAuthDialog({ tool, trigger }: PrebuiltAuthDialogProps) {
  const t = useTranslations('tool.authDialog')
  const [open, setOpen] = useState(false)

  if (!tool.provider_name) {
    const disabled = cloneWithProps(trigger, { disabled: true, 'aria-disabled': true })
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger render={disabled} />
          <TooltipContent>{t('legacyUnavailable')}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    )
  }

  const clickable = cloneWithProps(trigger, { onClick: () => setOpen(true) })
  return (
    <>
      {clickable}
      <ConnectionBindingDialog
        type="prebuilt"
        providerName={tool.provider_name}
        toolName={tool.name}
        open={open}
        onOpenChange={setOpen}
      />
    </>
  )
}

function cloneWithProps(
  node: React.ReactNode,
  props: Record<string, unknown>,
): React.ReactElement {
  if (!React.isValidElement(node)) {
    throw new Error('PrebuiltAuthDialog trigger must be a valid React element')
  }
  return React.cloneElement(node as React.ReactElement<Record<string, unknown>>, props)
}
