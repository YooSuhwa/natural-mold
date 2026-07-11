import type { ReactNode } from 'react'
import { ScopedIntlProvider } from '@/i18n/scoped-messages'
import { SkillStudioShell } from './_components/skill-studio-shell'

const SKILL_MESSAGE_NAMESPACES = ['credentials', 'marketplace', 'skill'] as const

export default function SkillsLayout({ children }: { children: ReactNode }) {
  return (
    <ScopedIntlProvider namespaces={SKILL_MESSAGE_NAMESPACES}>
      {/* 스튜디오 셸(탭바+컨텍스트 바)은 /skills 하위 전체를 감싼다. flex 체인
          (min-h-0)은 빌더 챗의 내부 스크롤 계약(app-layout → chat-client)을
          보존하기 위한 필수 조건 — Phase 2 스펙 AD-2. */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <SkillStudioShell />
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
      </div>
    </ScopedIntlProvider>
  )
}
