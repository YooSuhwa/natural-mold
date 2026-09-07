import * as React from 'react'

const MOBILE_BREAKPOINT = 768
const MOBILE_MEDIA_QUERY = `(max-width: ${MOBILE_BREAKPOINT - 1}px)`

function subscribeToMobileChanges(onStoreChange: () => void): () => void {
  const mediaQueryList = window.matchMedia(MOBILE_MEDIA_QUERY)
  mediaQueryList.addEventListener('change', onStoreChange)

  return () => mediaQueryList.removeEventListener('change', onStoreChange)
}

function getIsMobileSnapshot(): boolean {
  return window.matchMedia(MOBILE_MEDIA_QUERY).matches
}

function getServerIsMobileSnapshot(): boolean {
  return false
}

export function useIsMobile(): boolean {
  return React.useSyncExternalStore(
    subscribeToMobileChanges,
    getIsMobileSnapshot,
    getServerIsMobileSnapshot,
  )
}
