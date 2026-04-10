import { atom } from 'jotai'

export interface TokenUsage {
  inputTokens: number
  outputTokens: number
  cost: number
}

export const sessionTokenUsageAtom = atom<TokenUsage>({
  inputTokens: 0,
  outputTokens: 0,
  cost: 0,
})
