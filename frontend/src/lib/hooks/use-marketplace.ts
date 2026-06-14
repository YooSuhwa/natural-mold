'use client'

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { marketplaceApi } from '@/lib/api/marketplace'
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

const ITEMS_KEY = ['marketplace', 'items'] as const
const KSKILL_KEY = ['marketplace', 'admin', 'k-skill'] as const
const MODERATION_KEY = ['marketplace', 'admin', 'moderation'] as const
const AGENT_BLUEPRINTS_KEY = ['agent-blueprints'] as const
const MCP_SERVERS_KEY = ['mcp-servers'] as const

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
    queryKey: ['marketplace', 'items', filters ?? {}],
    queryFn: () => marketplaceApi.list(filters),
    staleTime: 30_000,
  })
}

export function useMarketplaceItemsPage(filters?: MarketplaceListFilters, enabled = true) {
  return useQuery({
    queryKey: ['marketplace', 'items', 'page', filters ?? {}],
    queryFn: () => marketplaceApi.page(filters),
    enabled,
    staleTime: 30_000,
  })
}

export function useMarketplaceItem(itemId: string | null | undefined) {
  return useQuery({
    queryKey: ['marketplace', 'items', itemId],
    queryFn: () => marketplaceApi.get(itemId!),
    enabled: !!itemId,
  })
}

export function useMarketplaceVersions(itemId: string | null | undefined) {
  return useQuery({
    queryKey: ['marketplace', 'items', itemId, 'versions'],
    queryFn: () => marketplaceApi.listVersions(itemId!),
    enabled: !!itemId,
  })
}

export function useMarketplaceVersion(versionId: string | null | undefined) {
  return useQuery({
    queryKey: ['marketplace', 'versions', versionId],
    queryFn: () => marketplaceApi.getVersion(versionId!),
    enabled: !!versionId,
  })
}

export function useModerationQueue(enabled = true) {
  return useQuery({
    queryKey: MODERATION_KEY,
    queryFn: () => marketplaceApi.moderationQueue(),
    enabled,
    staleTime: 15_000,
  })
}

export function useKSkillSyncStatus(enabled = true) {
  return useQuery({
    queryKey: KSKILL_KEY,
    queryFn: () => marketplaceApi.kSkillSyncStatus(),
    enabled,
    staleTime: 30_000,
  })
}

// ---------- Mutations ----------

function invalidateAllItems(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ITEMS_KEY })
  qc.invalidateQueries({ queryKey: ['skills'] })
  qc.invalidateQueries({ queryKey: AGENT_BLUEPRINTS_KEY })
  qc.invalidateQueries({ queryKey: MCP_SERVERS_KEY })
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
      qc.invalidateQueries({ queryKey: ['marketplace', 'items', itemId] })
      qc.invalidateQueries({ queryKey: ITEMS_KEY })
    },
  })
}

export function useRemoveItemACLEntry(itemId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (userId: string) => marketplaceApi.removeACLEntry(itemId, userId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['marketplace', 'items', itemId] })
      qc.invalidateQueries({ queryKey: ITEMS_KEY })
    },
  })
}

export function useDisableItem() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (itemId: string) => marketplaceApi.disableItem(itemId),
    onSuccess: () => {
      invalidateAllItems(qc)
      qc.invalidateQueries({ queryKey: MODERATION_KEY })
    },
  })
}

export function useEnableItem() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (itemId: string) => marketplaceApi.enableItem(itemId),
    onSuccess: () => {
      invalidateAllItems(qc)
      qc.invalidateQueries({ queryKey: MODERATION_KEY })
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
      qc.invalidateQueries({ queryKey: MODERATION_KEY })
    },
  })
}
