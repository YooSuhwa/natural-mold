import { describe, expect, it, vi } from 'vitest'

import { render, screen } from '../../../../tests/test-utils'
import { SkillDetailPackageFooter } from '../skill-detail-package-footer'

function renderFooter(overrides: Partial<Parameters<typeof SkillDetailPackageFooter>[0]> = {}) {
  return render(
    <SkillDetailPackageFooter
      savePending={false}
      saveDisabled={false}
      sizeBytes={2048}
      version="1.0.0"
      usedByCount={3}
      onSave={vi.fn()}
      {...overrides}
    />,
  )
}

describe('SkillDetailPackageFooter', () => {
  it('패키지 요약(크기·버전·연결 수)과 저장 버튼을 렌더한다 — Phase 2 소스 탭 계약', () => {
    renderFooter()

    // usedBy 요약 라인이 상시 노출 — 패키지 총 크기의 유일한 표시처(리뷰 R).
    expect(screen.getByText(/1\.0\.0/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '파일 저장' })).toBeEnabled()
    // 삭제/내보내기/닫기는 설정 탭·행 메뉴 소관 — 푸터에 없어야 한다.
    expect(screen.queryByRole('button', { name: '.skill 내보내기' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '스킬 삭제' })).not.toBeInTheDocument()
  })

  it('저장 비활성 상태를 반영한다', () => {
    renderFooter({ saveDisabled: true })

    expect(screen.getByRole('button', { name: '파일 저장' })).toBeDisabled()
  })
})
