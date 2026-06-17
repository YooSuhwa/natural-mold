'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { marketplaceApi } from '@/lib/api/marketplace'
import { agentBlueprintQueryKeys } from '@/lib/query-keys/agent-blueprints'
import { marketplaceQueryKeys } from '@/lib/query-keys/marketplace'
import { mcpServerQueryKeys } from '@/lib/query-keys/mcp-servers'
import { skillQueryKeys } from '@/lib/query-keys/skills'
import type {
  InstallMarketplaceItemBody,
  MarketplaceItemACLBody,
  MarketplaceItemPatchBody,
  MarketplaceListFilters,
  PublishAgentBody,
  PublishMcpServerBody,
  PublishNewVersionBody,
  PublishSkillBody,
  UpdateInstallationBody,
} from '@/lib/types/marketplace'

export {
  useAgentBlueprint,
  useAgentBlueprints,
  useCreateAgentFromBlueprint,
} from './use-agent-blueprints'

export {
  useDeleteSkillCredentialBinding,
  useSetSkillCredentialBinding,
  useSkillCredentialBindings,
  useSkillCredentialRequirements,
} from './use-skill-credential-bindings'

export function useMarketplaceItems(filters?: MarketplaceListFilters) {
  return useQuery({
    queryKey: marketplaceQueryKeys.itemList(filters),
    queryFn: () => marketplaceApi.list(filters),
    staleTime: 30_000,
  })
}

export function useMarketplaceItemsPage(filters?: MarketplaceListFilters, enabled = true) {
  return useQuery({
    queryKey: marketplaceQueryKeys.itemPage(filters),
    queryFn: () => marketplaceApi.page(filters),
    enabled,
    staleTime: 30_000,
  })
}

export function useMarketplaceItem(itemId: string | null | undefined) {
  return useQuery({
    queryKey: marketplaceQueryKeys.item(itemId),
    queryFn: () => marketplaceApi.get(itemId!),
    enabled: !!itemId,
  })
}

export function useMarketplaceVersions(itemId: string | null | undefined) {
  return useQuery({
    queryKey: marketplaceQueryKeys.itemVersions(itemId),
    queryFn: () => marketplaceApi.listVersions(itemId!),
    enabled: !!itemId,
  })
}

export function useMarketplaceVersion(versionId: string | null | undefined) {
  return useQuery({
    queryKey: marketplaceQueryKeys.version(versionId),
    queryFn: () => marketplaceApi.getVersion(versionId!),
    enabled: !!versionId,
  })
}

export function useModerationQueue(enabled = true) {
  return useQuery({
    queryKey: marketplaceQueryKeys.moderation,
    queryFn: () => marketplaceApi.moderationQueue(),
    enabled,
    staleTime: 15_000,
  })
}

export function useKSkillSyncStatus(enabled = true) {
  return useQuery({
    queryKey: marketplaceQueryKeys.kSkillAdmin,
    queryFn: () => marketplaceApi.kSkillSyncStatus(),
    enabled,
    staleTime: 30_000,
  })
}

// ---------- Mutations ----------

function invalidateAllItems(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: marketplaceQueryKeys.items })
  qc.invalidateQueries({ queryKey: skillQueryKeys.all })
  qc.invalidateQueries({ queryKey: agentBlueprintQueryKeys.all })
  qc.invalidateQueries({ queryKey: mcpServerQueryKeys.all })
}

export function useInstallItem(itemId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: InstallMarketplaceItemBody) => marketplaceApi.install(itemId, body),
    onSuccess: () => invalidateAllItems(qc),
  })
}

export function useUpdateInstallation(installationId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: UpdateInstallationBody) =>
      marketplaceApi.updateInstallation(installationId, body),
    onSuccess: () => invalidateAllItems(qc),
  })
}

export function useDeleteInstallation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      installationId,
      deleteResource = false,
    }: {
      installationId: string
      deleteResource?: boolean
    }) => marketplaceApi.deleteInstallation(installationId, deleteResource),
    onSuccess: () => invalidateAllItems(qc),
  })
}

export function usePublishSkill() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ skillId, body }: { skillId: string; body: PublishSkillBody }) =>
      marketplaceApi.publishFromSkill(skillId, body),
    onSuccess: () => invalidateAllItems(qc),
  })
}

export function usePublishMcpServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ serverId, body }: { serverId: string; body: PublishMcpServerBody }) =>
      marketplaceApi.publishFromMcp(serverId, body),
    onSuccess: () => invalidateAllItems(qc),
  })
}

export function usePublishAgent() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ agentId, body }: { agentId: string; body: PublishAgentBody }) =>
      marketplaceApi.publishFromAgent(agentId, body),
    onSuccess: () => invalidateAllItems(qc),
  })
}

export function usePublishNewVersion() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      itemId,
      skillId,
      body,
    }: {
      itemId: string
      skillId: string
      body: PublishNewVersionBody
    }) => marketplaceApi.publishNewVersion(itemId, skillId, body),
    onSuccess: () => invalidateAllItems(qc),
  })
}

export function usePublishNewMcpVersion() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      itemId,
      serverId,
      body,
    }: {
      itemId: string
      serverId: string
      body: PublishNewVersionBody
    }) => marketplaceApi.publishNewMcpVersion(itemId, serverId, body),
    onSuccess: () => invalidateAllItems(qc),
  })
}

export function usePublishNewAgentVersion() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      itemId,
      agentId,
      body,
    }: {
      itemId: string
      agentId: string
      body: PublishNewVersionBody
    }) => marketplaceApi.publishNewAgentVersion(itemId, agentId, body),
    onSuccess: () => invalidateAllItems(qc),
  })
}

export function usePatchMarketplaceItem(itemId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: MarketplaceItemPatchBody) => marketplaceApi.patchItem(itemId, body),
    onSuccess: () => invalidateAllItems(qc),
  })
}

export function useReplaceItemACL(itemId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: MarketplaceItemACLBody) => marketplaceApi.replaceACL(itemId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: marketplaceQueryKeys.item(itemId) })
      qc.invalidateQueries({ queryKey: marketplaceQueryKeys.items })
    },
  })
}

export function useRemoveItemACLEntry(itemId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (userId: string) => marketplaceApi.removeACLEntry(itemId, userId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: marketplaceQueryKeys.item(itemId) })
      qc.invalidateQueries({ queryKey: marketplaceQueryKeys.items })
    },
  })
}

export function useDisableItem() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (itemId: string) => marketplaceApi.disableItem(itemId),
    onSuccess: () => {
      invalidateAllItems(qc)
      qc.invalidateQueries({ queryKey: marketplaceQueryKeys.moderation })
    },
  })
}

export function useEnableItem() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (itemId: string) => marketplaceApi.enableItem(itemId),
    onSuccess: () => {
      invalidateAllItems(qc)
      qc.invalidateQueries({ queryKey: marketplaceQueryKeys.moderation })
    },
  })
}

export function useRemoveItemACL(itemId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (userId: string) => marketplaceApi.removeACLEntry(itemId, userId),
    onSuccess: () => invalidateAllItems(qc),
  })
}

export function useAdminSetListed() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ itemId, isListed }: { itemId: string; isListed: boolean }) =>
      marketplaceApi.adminSetListed(itemId, isListed),
    onSuccess: () => {
      invalidateAllItems(qc)
      qc.invalidateQueries({ queryKey: marketplaceQueryKeys.moderation })
    },
  })
}
