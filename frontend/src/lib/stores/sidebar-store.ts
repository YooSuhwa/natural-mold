import { atom } from 'jotai'
import { atomWithStorage } from 'jotai/utils'

export const sidebarOpenAtom = atom(true)

export const connectorsExpandedAtom = atomWithStorage('moldy.sidebar.connectorsExpanded', true)

export const marketplaceExpandedAtom = atomWithStorage('moldy.sidebar.marketplaceExpanded', true)
