'use client'

import { lazy } from 'react'

export const BuilderAssistantMessage = lazy(() =>
  import('@/components/chat/builder-overrides').then((module) => ({
    default: module.BuilderAssistantMessage,
  })),
)

export const BuilderAssistantMessageParts = lazy(() =>
  import('@/components/chat/builder-overrides').then((module) => ({
    default: module.BuilderAssistantMessageParts,
  })),
)

export const BuilderComposer = lazy(() =>
  import('@/components/chat/builder-overrides').then((module) => ({
    default: module.BuilderComposer,
  })),
)

export const BuilderUserEditComposer = lazy(() =>
  import('@/components/chat/builder-overrides').then((module) => ({
    default: module.BuilderUserEditComposer,
  })),
)

export const BuilderUserMessage = lazy(() =>
  import('@/components/chat/builder-overrides').then((module) => ({
    default: module.BuilderUserMessage,
  })),
)

export function BuilderMessageFallback() {
  return <div className="min-h-12" aria-hidden />
}

export function BuilderComposerFallback() {
  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-4">
      <div className="moldy-card h-20 animate-pulse" aria-hidden />
    </div>
  )
}
