import type { ReactNode } from 'react'
import { ScopedIntlProvider } from '@/i18n/scoped-messages'

// 빌더 챗은 메인챗 서피스(ChatRuntimeSection)를 그대로 마운트하므로 `chat`
// 네임스페이스가 필요하다 — 상위 /skills 레이아웃 스코프(credentials/
// marketplace/skill)에는 없다 (scoped-messages 함정: 스코프 밖 컴포넌트는
// raw i18n 키를 렌더한다). agents 챗 레이아웃과 동일 구성 + skill 유지.
const BUILDER_CHAT_MESSAGE_NAMESPACES = ['agent', 'chat', 'model', 'skill', 'usage'] as const

export default function SkillBuilderChatLayout({ children }: { children: ReactNode }) {
  return (
    <ScopedIntlProvider namespaces={BUILDER_CHAT_MESSAGE_NAMESPACES}>{children}</ScopedIntlProvider>
  )
}
