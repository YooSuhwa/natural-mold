import { Suspense } from 'react'

import { SkillBuilderIndexClient } from './_components/skill-builder-index-client'

/** 빌더 인덱스 — 세션 이력 + 시작 CTA. useSearchParams CSR bailout 대응 Suspense. */
export default function SkillBuilderIndexPage() {
  return (
    <Suspense fallback={null}>
      <SkillBuilderIndexClient />
    </Suspense>
  )
}
