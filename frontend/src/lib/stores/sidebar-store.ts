import { atom } from 'jotai'
import { atomWithStorage } from 'jotai/utils'

export const sidebarOpenAtom = atom(true)

export const featuresExpandedAtom = atomWithStorage('moldy.sidebar.featuresExpanded', true)
