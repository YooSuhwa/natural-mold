'use client'

import React, { useState } from 'react'

import { ConnectionBindingDialog } from '@/components/connection/connection-binding-dialog'
import { CustomAuthDialog } from '@/components/tool/custom-auth-dialog'
import { isPrebuiltProviderName, type Tool } from '@/lib/types'

interface PrebuiltAuthDialogProps {
  tool: Tool
  trigger: React.ReactNode
}

/**
 * PREBUILT 도구 인증 dialog.
 *
 * ADR-008: PREBUILT는 per-user Connection 엔티티(user_id+type+provider_name)로
 * credential을 바인딩한다. tool row 자체는 공유 행이라 mutate하지 않는다.
 *
 * tool.provider_name이 null인 경우(m10 매핑 실패 — 실무상 0건 예상이지만
 * 존재 가능)는 backend가 여전히 legacy `tool.credential_id` 경로로 실행하므로,
 * UI도 legacy credential-edit 플로우(CustomAuthDialog)로 위임해 rotate/clear/
 * repair 경로를 유지한다. 그렇지 않으면 "도구는 실행되지만 관리 불가" 운영
 * 데드엔드가 발생 (Codex adversarial 4차 P2). M6 cleanup에서 legacy path 일괄 제거.
 */
export function PrebuiltAuthDialog({ tool, trigger }: PrebuiltAuthDialogProps) {
  const [open, setOpen] = useState(false)

  // Legacy path (provider_name NULL이거나 알 수 없는 값) — CustomAuthDialog는
  // tool.credential_id 기반의 기존 credential-edit 플로우를 제공한다. 백필 실패
  // row나 M3 이후 추가된 미등록 provider를 UI에서 계속 관리 가능하게 한다.
  // M4에서 교체 예정.
  if (!isPrebuiltProviderName(tool.provider_name)) {
    return <CustomAuthDialog tool={tool} trigger={trigger} />
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
