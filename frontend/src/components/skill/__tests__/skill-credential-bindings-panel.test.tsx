import { beforeEach, describe, expect, it, vi } from 'vitest'

import { render, screen, within } from '../../../../tests/test-utils'
import type { CredentialRequirement, SkillCredentialBinding } from '@/lib/types/marketplace'

import { SkillCredentialBindingsPanel } from '../skill-credential-bindings-panel'

const mockUseSkillCredentialRequirements = vi.fn()
const mockUseSkillCredentialBindings = vi.fn()
const mockSetBinding = vi.fn()
const mockDeleteBinding = vi.fn()

vi.mock('@/components/credential/credential-picker', () => ({
  CredentialPicker: ({ placeholder }: { readonly placeholder: string }) => (
    <button type="button">{placeholder}</button>
  ),
}))

vi.mock('@/lib/hooks/use-marketplace', () => ({
  useSkillCredentialRequirements: (...args: readonly unknown[]) =>
    mockUseSkillCredentialRequirements(...args),
  useSkillCredentialBindings: (...args: readonly unknown[]) =>
    mockUseSkillCredentialBindings(...args),
  useSetSkillCredentialBinding: () => ({
    mutateAsync: mockSetBinding,
    isPending: false,
  }),
  useDeleteSkillCredentialBinding: () => ({
    mutateAsync: mockDeleteBinding,
    isPending: false,
  }),
}))

function buildRequirement(overrides: Partial<CredentialRequirement>): CredentialRequirement {
  return {
    key: 'weather_key',
    definition_key: 'weather_api',
    required: true,
    label: 'Weather API',
    description: '날씨 조회에 사용합니다.',
    fields: ['api_key'],
    injection: 'env',
    scope: 'user',
    ...overrides,
  }
}

function buildBinding(overrides: Partial<SkillCredentialBinding>): SkillCredentialBinding {
  return {
    id: 'binding-1',
    requirement_key: 'weather_key',
    credential_id: 'credential-1',
    scope: 'skill',
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
    ...overrides,
  }
}

describe('SkillCredentialBindingsPanel', () => {
  beforeEach(() => {
    mockUseSkillCredentialRequirements.mockReset()
    mockUseSkillCredentialBindings.mockReset()
    mockSetBinding.mockReset()
    mockDeleteBinding.mockReset()
  })

  it('shows missing required credentials with per-requirement binding state', () => {
    mockUseSkillCredentialRequirements.mockReturnValue({
      data: [
        buildRequirement({ key: 'weather_key', required: true, definition_key: 'weather_api' }),
        buildRequirement({
          key: 'region_key',
          required: false,
          label: 'Region',
          definition_key: 'region_api',
        }),
      ],
      isLoading: false,
    })
    mockUseSkillCredentialBindings.mockReturnValue({
      data: [buildBinding({ requirement_key: 'region_key', credential_id: 'credential-region' })],
      isLoading: false,
    })

    render(<SkillCredentialBindingsPanel skillId="skill-1" />)

    expect(screen.getByText('필수 자격증명 1개 미연결')).toBeInTheDocument()

    const weatherRow = screen.getByText('Weather API').closest('div')
    if (weatherRow === null) {
      throw new Error('Weather requirement row was not rendered')
    }
    expect(within(weatherRow).getByText('필수')).toBeInTheDocument()
    expect(within(weatherRow).getByText('미연결')).toBeInTheDocument()
    expect(screen.getByText('weather_api')).toBeInTheDocument()

    const regionRow = screen.getByText('Region').closest('div')
    if (regionRow === null) {
      throw new Error('Region requirement row was not rendered')
    }
    expect(within(regionRow).getByText('선택')).toBeInTheDocument()
    expect(within(regionRow).getByText('연결됨')).toBeInTheDocument()
    expect(screen.getByText('region_api')).toBeInTheDocument()
  })

  it('shows an all-bound summary when every required credential is connected', () => {
    mockUseSkillCredentialRequirements.mockReturnValue({
      data: [buildRequirement({ key: 'weather_key', required: true })],
      isLoading: false,
    })
    mockUseSkillCredentialBindings.mockReturnValue({
      data: [buildBinding({ requirement_key: 'weather_key' })],
      isLoading: false,
    })

    render(<SkillCredentialBindingsPanel skillId="skill-1" />)

    expect(screen.getByText('필수 자격증명이 모두 연결되었습니다')).toBeInTheDocument()
    expect(screen.getByText('연결됨')).toBeInTheDocument()
  })
})
