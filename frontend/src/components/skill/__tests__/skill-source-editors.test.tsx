import { describe, expect, it, vi, beforeEach } from 'vitest'

import { render, screen, userEvent } from '../../../../tests/test-utils'
import type { SkillDetailTabSlots } from '../skill-detail-tab-shell'

import { TextSkillEditor } from '../skill-detail-text-editor'
import { PackageSkillEditor } from '../skill-detail-package-editor'

// 소스 탭 편집·저장 루프 회귀 가드 — 구 다이얼로그 테스트 삭제로 생긴
// 커버리지 공백을 메운다 (리뷰 R / B3).

function renderTestSlots(slots: SkillDetailTabSlots) {
  return (
    <>
      {slots.sidebar}
      {slots.body}
      {slots.footer}
      {slots.overlay}
    </>
  )
}

const mockUpdateContent = vi.fn()
const mockSetFile = vi.fn()
const mockDeleteFile = vi.fn()

vi.mock('@/lib/hooks/use-skills', () => ({
  useSkill: () => ({
    data: {
      id: 'skill-1',
      kind: 'package',
      size_bytes: 2048,
      version: '1.0.0',
      used_by_count: 1,
      content_hash: 'hash-1',
    },
  }),
  useSkillContent: () => ({ data: { content: '# 원본 본문' } }),
  useUpdateSkillContent: () => ({ mutateAsync: mockUpdateContent, isPending: false }),
  useSkillFiles: () => ({
    data: [
      { path: 'SKILL.md', is_dir: false, size: 128 },
      { path: 'scripts/run.py', is_dir: false, size: 64 },
    ],
  }),
  useSetSkillFile: () => ({ mutateAsync: mockSetFile, isPending: false }),
  useDeleteSkillFile: () => ({ mutateAsync: mockDeleteFile, isPending: false }),
}))

vi.mock('../use-skill-file-remote-cache', () => ({
  useSkillFileRemoteCache: () => ({
    remoteCache: new Map([
      ['SKILL.md', '# SKILL.md 원본'],
      ['scripts/run.py', 'print("hi")'],
    ]),
    setRemoteCache: vi.fn(),
  }),
}))

describe('TextSkillEditor (소스 탭)', () => {
  beforeEach(() => {
    mockUpdateContent.mockReset().mockResolvedValue(undefined)
  })

  it('본문을 수정하고 저장하면 PUT 페이로드에 편집 내용이 실린다', async () => {
    const user = userEvent.setup()
    render(<TextSkillEditor skillId="skill-1">{renderTestSlots}</TextSkillEditor>)

    const textarea = screen.getByRole('textbox')
    expect(textarea).toHaveValue('# 원본 본문')

    await user.clear(textarea)
    await user.type(textarea, '# 수정된 본문')
    await user.click(screen.getByRole('button', { name: '저장' }))

    expect(mockUpdateContent).toHaveBeenCalledWith({
      id: 'skill-1',
      data: { content: '# 수정된 본문' },
    })
  })
})

describe('PackageSkillEditor (소스 탭)', () => {
  beforeEach(() => {
    mockSetFile.mockReset().mockResolvedValue(undefined)
  })

  it('파일을 수정하면 저장이 활성화되고 PUT에 경로·내용이 실린다', async () => {
    const user = userEvent.setup()
    render(<PackageSkillEditor skillId="skill-1">{renderTestSlots}</PackageSkillEditor>)

    // SKILL.md가 기본 선택 — 원본 내용 렌더.
    const textarea = screen.getByRole('textbox')
    expect(textarea).toHaveValue('# SKILL.md 원본')
    const save = screen.getByRole('button', { name: '파일 저장' })
    expect(save).toBeDisabled()

    await user.clear(textarea)
    await user.type(textarea, '# 갱신')
    expect(save).toBeEnabled()

    await user.click(save)
    expect(mockSetFile).toHaveBeenCalledWith({ path: 'SKILL.md', content: '# 갱신' })
  })
})
