'use client'

import { createContext, type ReactNode, useContext, useSyncExternalStore } from 'react'

import type {
  ServerMessageQueueController,
  ServerMessageQueueSnapshot,
} from './server-message-queue-contract'

const ServerMessageQueueContext = createContext<ServerMessageQueueController | null>(null)

export function ServerMessageQueueProvider({
  controller,
  children,
}: {
  readonly controller: ServerMessageQueueController
  readonly children: ReactNode
}) {
  return (
    <ServerMessageQueueContext.Provider value={controller}>
      {children}
    </ServerMessageQueueContext.Provider>
  )
}

export function useServerMessageQueueController(): ServerMessageQueueController {
  const controller = useContext(ServerMessageQueueContext)
  if (!controller) {
    throw new Error('ServerMessageQueueProvider is missing')
  }
  return controller
}

export function useServerMessageQueueSnapshot(): ServerMessageQueueSnapshot {
  const controller = useServerMessageQueueController()
  return useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot)
}
