import { describe, expect, it } from 'vitest'
import { renderHook } from '@testing-library/react'
import { ClockIcon, WrenchIcon } from 'lucide-react'
import { getDomainIcon } from '@/components/shared/icon'
import { ToolIconProvider, useToolIcon } from '../tool-icon-context'

function wrapper(iconIds: Record<string, string>) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <ToolIconProvider iconIds={iconIds}>{children}</ToolIconProvider>
  }
}

describe('useToolIcon', () => {
  it('빌트인 맵이 1순위 (icon_id가 있어도 빌트인 우선)', () => {
    const { result } = renderHook(() => useToolIcon('current_datetime'), {
      wrapper: wrapper({ current_datetime: 'calendar' }),
    })
    expect(result.current).toBe(ClockIcon)
  })

  it('빌트인에 없으면 도구 icon_id를 getDomainIcon으로 해석', () => {
    const { result } = renderHook(() => useToolIcon('custom_registry_tool'), {
      wrapper: wrapper({ custom_registry_tool: 'calendar' }),
    })
    expect(result.current).toBe(getDomainIcon('calendar'))
  })

  it('빌트인도 icon_id도 없으면 렌치 폴백', () => {
    const { result } = renderHook(() => useToolIcon('unknown_mcp_tool'), {
      wrapper: wrapper({}),
    })
    expect(result.current).toBe(WrenchIcon)
  })
})
