export const DIALOG_SIZE = {
  sm: 'w-[400px]',
  md: 'w-[560px]',
  lg: 'w-[720px]',
  xl: 'w-[920px]',
  console: 'w-[1080px]',
} as const

export const DIALOG_HEIGHT = {
  auto: 'h-[480px] max-h-[calc(100vh-4rem)]',
  fixed: 'h-[640px] max-h-[calc(100vh-4rem)]',
  tall: 'h-[760px] max-h-[calc(100vh-4rem)]',
} as const

export type DialogSize = keyof typeof DIALOG_SIZE
export type DialogHeight = keyof typeof DIALOG_HEIGHT
