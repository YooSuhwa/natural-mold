import { Suspense } from 'react'
import { SkillsPageClient } from './_components/skills-page-client'

export default function SkillsPage() {
  // SkillsPageClient가 딥링크(detailId)를 useSearchParams로 읽으므로
  // Suspense 경계가 필요하다 (Next CSR bailout 규칙).
  return (
    <Suspense>
      <SkillsPageClient />
    </Suspense>
  )
}
