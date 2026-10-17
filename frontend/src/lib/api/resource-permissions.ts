import { apiFetch } from './client'

/** Resource types whose list bodies carry no `my_permissions` (H-05). */
export type PermissionResourceType = 'tool' | 'mcp_server' | 'credential' | 'skill'

export interface ResourcePermissionList {
  items: { id: string; my_permissions: string[] }[]
}

/** Server cap per request (`MAX_IDS`). */
export const RESOURCE_PERMISSION_BATCH = 100

/** `GET /api/resources/{type}/permissions?ids=…`; hidden ids are simply absent. */
export const resourcePermissionsApi = {
  list: (type: PermissionResourceType, ids: readonly string[]) => {
    const query = ids.map((id) => `ids=${encodeURIComponent(id)}`).join('&')
    return apiFetch<ResourcePermissionList>(`/api/resources/${type}/permissions?${query}`)
  },
}
