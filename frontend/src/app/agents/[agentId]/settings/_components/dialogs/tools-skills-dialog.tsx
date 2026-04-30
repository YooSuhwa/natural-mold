'use client'

/**
 * Settings 페이지 form-mode가 사용하는 도구·스킬 추가 다이얼로그.
 *
 * Visual-settings의 통합 4탭 다이얼로그(Catalog / My Tools / MCP / Skills)
 * 와 동일한 UX를 form-mode에서도 쓰도록 그대로 재export. 컴포넌트 본체는
 * ``@/components/agent/visual-settings/dialogs/tools-skills-dialog``에 살고,
 * settings 페이지는 useTools/useSkills로 로드한 list를 prop으로 넘긴다.
 */

export { ToolsSkillsDialog } from '@/components/agent/visual-settings/dialogs/tools-skills-dialog'
