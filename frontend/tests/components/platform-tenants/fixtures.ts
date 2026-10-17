import type {
  PlatformTenant,
  PlatformTenantExport,
  PlatformTenantUsage,
} from '@/lib/types/platform-tenant'

export function tenantFixture(overrides: Partial<PlatformTenant> = {}): PlatformTenant {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    name: '가나전자',
    slug: 'gana',
    status: 'active',
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-17T03:00:00.123456Z',
    limits: {
      member_limit: 500,
      monthly_spend_limit: '1000',
      concurrent_run_limit: 50,
      allow_system_models: true,
    },
    organization_count: 5,
    active_member_count: 412,
    ...overrides,
  }
}

export const usageFixture: PlatformTenantUsage = {
  used: '812.5',
  monthly_spend_limit: '1000',
  resets_at: '2026-11-01T00:00:00Z',
  member_limit: 500,
  members: 412,
  concurrent_run_limit: 50,
  active_runs: 3,
}

export function exportFixture(overrides: Partial<PlatformTenantExport> = {}): PlatformTenantExport {
  return {
    id: '22222222-2222-4222-8222-222222222222',
    tenant_id: '11111111-1111-4111-8111-111111111111',
    status: 'succeeded',
    progress: 100,
    error_code: null,
    size_bytes: 2048,
    created_at: '2026-10-16T00:00:00Z',
    started_at: '2026-10-16T00:00:01Z',
    completed_at: '2026-10-16T00:01:00Z',
    expires_at: '2026-10-23T00:01:00Z',
    download_available: true,
    ...overrides,
  }
}

export function platformApiMock() {
  return {
    installation: vi.fn(),
    list: vi.fn(),
    get: vi.fn(),
    usage: vi.fn(),
    deletion: vi.fn(),
    exports: vi.fn(),
    exportDownloadPath: vi.fn((id: string, exportId: string) => `/x/${id}/${exportId}`),
    create: vi.fn(),
    update: vi.fn(),
    suspend: vi.fn(),
    resume: vi.fn(),
    startExport: vi.fn(),
    scheduleDeletion: vi.fn(),
    cancelDeletion: vi.fn(),
  }
}
