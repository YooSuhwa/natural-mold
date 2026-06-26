'use client'

import { createContext, useContext, type ReactNode } from 'react'

// ToolGroupContainer가 자식(연속 같은 도구 호출)을 렌더할 때 이 컨텍스트를 켠다.
// 자식 pill은 이를 읽어 "그룹 안"임을 알고, 도구명 대신 호출별 인자/결과 요약을
// 제목으로 보여준다(도구명은 그룹 헤더에 이미 있으므로 중복 제거).

const ToolGroupChildContext = createContext(false)

export function ToolGroupChildProvider({ children }: { children: ReactNode }) {
  return <ToolGroupChildContext.Provider value={true}>{children}</ToolGroupChildContext.Provider>
}

/** 현재 렌더가 tool 그룹 컨테이너의 자식인지. */
export function useIsToolGroupChild(): boolean {
  return useContext(ToolGroupChildContext)
}
