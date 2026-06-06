'use client'

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  artifactKeys,
  getArtifactLibraryStats,
  listArtifactLibrary,
  listRecentArtifacts,
  recordArtifactOpened,
  setArtifactFavorite,
} from '@/lib/api/artifacts'
import type { ArtifactLibraryParams } from '@/lib/types'

export function useArtifactLibrary(params: ArtifactLibraryParams) {
  return useInfiniteQuery({
    queryKey: artifactKeys.library(params),
    queryFn: ({ pageParam }) => listArtifactLibrary({ ...params, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    staleTime: 10_000,
  })
}

export function useArtifactLibraryStats() {
  return useQuery({
    queryKey: artifactKeys.stats,
    queryFn: getArtifactLibraryStats,
    staleTime: 10_000,
  })
}

export function useRecentArtifacts(limit = 20) {
  return useQuery({
    queryKey: artifactKeys.recent(limit),
    queryFn: () => listRecentArtifacts(limit),
    staleTime: 10_000,
  })
}

export function useSetArtifactFavorite() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { artifactId: string; isFavorite: boolean }) =>
      setArtifactFavorite(input.artifactId, input.isFavorite),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: artifactKeys.all })
    },
  })
}

export function useRecordArtifactOpened() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (artifactId: string) => recordArtifactOpened(artifactId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: artifactKeys.all })
    },
  })
}
