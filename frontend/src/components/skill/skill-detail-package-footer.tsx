'use client'

import { Loader2, Save } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'

type SkillDetailPackageFooterProps = {
  readonly savePending: boolean
  readonly saveDisabled: boolean
  readonly sizeBytes: number
  readonly version: string | null
  readonly usedByCount: number
  readonly onSave: () => void
}

/**
 * 소스 탭(패키지) 푸터 — 저장 + 패키지 요약(크기·버전·연결 수).
 * 삭제/내보내기/닫기는 스튜디오 설정 탭·행 메뉴가 소유한다 (Phase 2 D1).
 */
export function SkillDetailPackageFooter({
  savePending,
  saveDisabled,
  sizeBytes,
  version,
  usedByCount,
  onSave,
}: SkillDetailPackageFooterProps) {
  const t = useTranslations('skill.detailDialog')

  return (
    <>
      <span className="moldy-ui-micro mr-auto text-muted-foreground">
        {t('usedBy', {
          bytes: sizeBytes,
          version: version ?? '—',
          count: usedByCount,
        })}
      </span>
      <Button onClick={onSave} disabled={saveDisabled}>
        {savePending ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
        {t('saveFile')}
      </Button>
    </>
  )
}
