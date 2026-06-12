'use client'

import { useEffect } from 'react'
import { useSetAtom } from 'jotai'
import { useRouter } from 'next/navigation'
import { shortcutPreviewActiveAtom } from '@/lib/stores/chat-navigator-store'

interface ChatNavigatorShortcutsOptions {
  onOpenQuickSwitcher: () => void
  onEscape: () => void
}

function isMacPlatform(): boolean {
  if (typeof navigator === 'undefined') return false
  // navigator.platform은 deprecated — 빈 값을 주는 브라우저는 userAgent로 판별
  return /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent)
}

export function formatShortcutLabel(index: number, mac = isMacPlatform()): string {
  return mac ? `⌘⇧${index}` : `Ctrl+Shift+${index}`
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable
}

function sessionHrefAt(index: number): string | null {
  // 접힌 그룹/숨은 패널의 행이 인덱스를 밀지 않도록 화면에 보이는 행만 센다
  const rows = Array.from(
    document.querySelectorAll<HTMLElement>('[data-chat-session-href]'),
  ).filter((row) => (typeof row.checkVisibility === 'function' ? row.checkVisibility() : true))
  const row = rows[index - 1]
  return row?.dataset.chatSessionHref ?? null
}

export function useChatNavigatorShortcuts({
  onOpenQuickSwitcher,
  onEscape,
}: ChatNavigatorShortcutsOptions): void {
  const router = useRouter()
  const setShortcutPreviewActive = useSetAtom(shortcutPreviewActiveAtom)

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      // IME 조합 중 키 입력은 단축키가 아니다 (한글 등 조합 입력 보호)
      if (event.isComposing) return
      if (event.metaKey || event.ctrlKey) setShortcutPreviewActive(true)
      if ((event.metaKey || event.ctrlKey) && event.code === 'KeyK') {
        event.preventDefault()
        onOpenQuickSwitcher()
        return
      }
      // Shift 조합 시 event.key는 레이아웃별 문자('!')가 되므로 물리 키 코드로 판별
      const digitMatch = /^Digit([1-9])$/.exec(event.code)
      if ((event.metaKey || event.ctrlKey) && event.shiftKey && digitMatch) {
        // 입력 요소 포커스 중 내비게이션은 작성 중인 내용을 유실시킨다 (Cmd+K 팔레트는 전역 유지)
        if (isEditableTarget(event.target)) return
        const href = sessionHrefAt(Number(digitMatch[1]))
        if (href) {
          event.preventDefault()
          router.push(href)
        }
        return
      }
      if (event.key === 'Escape') onEscape()
    }

    function handleKeyUp(event: KeyboardEvent) {
      if (!event.metaKey && !event.ctrlKey) setShortcutPreviewActive(false)
    }

    function handleBlur() {
      setShortcutPreviewActive(false)
    }

    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('keyup', handleKeyUp)
    window.addEventListener('blur', handleBlur)
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('keyup', handleKeyUp)
      window.removeEventListener('blur', handleBlur)
    }
  }, [onEscape, onOpenQuickSwitcher, router, setShortcutPreviewActive])
}
