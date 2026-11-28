'use client'

import { createContext } from 'react'

/** AG-UI publishes the successful run submitted in this view. Loading a stored run has no receipt. */
export const AgUiFollowupCompletionContext = createContext<string | null | undefined>(undefined)
