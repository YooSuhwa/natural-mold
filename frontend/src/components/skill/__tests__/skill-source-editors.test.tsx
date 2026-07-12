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
// 서버 콘텐츠 변화(롤백 등) 시뮬레이션용 가변 홀더 — 렌더마다 최신 값을 읽는다.
const textContentHolder = { content: '# 원본 본문' }

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
  useSkillContent: () => ({ data: { content: textContentHolder.content } }),
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
    textContentHolder.content = '# 원본 본문'
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

  it('서버 콘텐츠가 바뀌면(롤백 후 refetch) 에디터를 재시드한다 — R5', () => {
    const { rerender } = render(
      <TextSkillEditor skillId="skill-1">{renderTestSlots}</TextSkillEditor>,
    )
    expect(screen.getByRole('textbox')).toHaveValue('# 원본 본문')

    // 버전 탭 롤백 → content 쿼리 refetch가 새 본문으로 착지.
    textContentHolder.content = '# 롤백된 본문'
    rerender(<TextSkillEditor skillId="skill-1">{renderTestSlots}</TextSkillEditor>)

    // hydrate-once였다면 stale '# 원본 본문'이 잠겨 저장 시 롤백을 되돌린다.
    expect(screen.getByRole('textbox')).toHaveValue('# 롤백된 본문')
  })

  it('dirty draft는 서버 콘텐츠 변화가 착지해도 덮이지 않는다', async () => {
    const user = userEvent.setup()
    const { rerender } = render(
      <TextSkillEditor skillId="skill-1">{renderTestSlots}</TextSkillEditor>,
    )

    const textarea = screen.getByRole('textbox')
    await user.clear(textarea)
    await user.type(textarea, '# 편집 중')

    textContentHolder.content = '# 백그라운드 갱신'
    rerender(<TextSkillEditor skillId="skill-1">{renderTestSlots}</TextSkillEditor>)

    expect(screen.getByRole('textbox')).toHaveValue('# 편집 중')
  })
})

describe('PackageSkillEditor (소스 탭)', () => {
  beforeEach(() => {
    mockSetFile.mockReset().mockResolvedValue(undefined)
  })

  it('파일 추가는 중복 경로를 거부하고, 새 경로는 선행 슬래시를 벗겨 생성한다', async () => {
    const user = userEvent.setup()
    render(<PackageSkillEditor skillId="skill-1">{renderTestSlots}</PackageSkillEditor>)

    await user.click(screen.getByRole('button', { name: '파일 추가' }))
    const pathInput = screen.getByPlaceholderText('path/to/new-file.md')

    // 중복 경로 거부 — 기존 파일이 빈 내용으로 덮이면 안 된다.
    await user.type(pathInput, 'SKILL.md')
    await user.click(screen.getByRole('button', { name: '생성' }))
    expect(mockSetFile).not.toHaveBeenCalled()

    await user.clear(pathInput)
    await user.type(pathInput, '/references/new.md')
    await user.click(screen.getByRole('button', { name: '생성' }))
    expect(mockSetFile).toHaveBeenCalledWith({ path: 'references/new.md', content: '' })
  })

  it('SKILL.md 외 파일은 삭제 확인 후 DELETE를 부른다', async () => {
    const user = userEvent.setup()
    render(<PackageSkillEditor skillId="skill-1">{renderTestSlots}</PackageSkillEditor>)

    // 삭제 버튼은 SKILL.md(보호됨) 외 파일에서만 노출된다. 트리는 leaf 이름만
    // 표시하므로 run.py로 선택한다.
    await user.click(screen.getByRole('button', { name: /run\.py/ }))
    await user.click(screen.getByRole('button', { name: '파일 삭제' }))
    await user.click(screen.getByRole('button', { name: '삭제 확인' }))

    expect(mockDeleteFile).toHaveBeenCalledWith('scripts/run.py')
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
