/**
 * B-09 harness (plan 13.10/13.11): after an organization switch the previous
 * organization's id — and the ids of its resources — appear zero times in the
 * rendered DOM, the TanStack query cache, the Jotai store and web storage, and
 * in-flight org-scoped requests/streams are aborted.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { useAtom, useSetAtom, useStore } from 'jotai'
import { NextIntlClientProvider } from 'next-intl'
import { useEffect, useState, type ReactNode } from 'react'
import { usePathname } from 'next/navigation'

import messages from '../../../messages/ko.json'
import { meOrganizationsApi } from '@/lib/api/organizations'
import { apiFetch } from '@/lib/api/client'
import { organizationQueryKeys } from '@/lib/query-keys/organizations'
import {
  LANDING_TIMEOUT_MS,
  OrgContextProvider,
  useOrgContext,
} from '@/lib/providers/org-context-provider'
import { useRegisterUnsavedOrgDraft } from '@/lib/org-scope/unsaved-drafts'
import { finishOrgSwitch, withOrgScopeSignal } from '@/lib/org-scope/org-scope-signal'
import { createMoldyAgentTransport } from '@/lib/chat/langgraph-runtime/moldy-agent-transport'
import { storeFixInitialMessage } from '@/lib/agents/fix-message-handoff'
import * as chatArtifactsStore from '@/lib/stores/chat-artifacts'
import * as chatDataUiStore from '@/lib/stores/chat-data-ui'
import * as chatFollowupStore from '@/lib/stores/chat-followup'
import * as chatMemoryRecallStore from '@/lib/stores/chat-memory-recall'
import * as chatNavigatorStore from '@/lib/stores/chat-navigator-store'
import * as chatRightRailStore from '@/lib/stores/chat-right-rail'
import * as chatSkillBuilderStore from '@/lib/stores/chat-skill-builder'
import * as chatStore from '@/lib/stores/chat-store'
import * as chatSubagentNamesStore from '@/lib/stores/chat-subagent-names'
import * as sidebarStore from '@/lib/stores/sidebar-store'
import { makeOrganization } from '../../unit/hooks/organization-fixtures'

const nav = vi.hoisted(() => {
  const listeners = new Set<() => void>()
  const state = {
    pathname: '/agents/agent-prev-1/conversations/conv-prev-1',
    duringReplace: null as null | (() => void),
  }
  return {
    state,
    listeners,
    replace: vi.fn((path: string) => {
      state.duringReplace?.()
      state.pathname = path
      for (const listener of listeners) listener()
    }),
  }
})
vi.mock('next/navigation', async () => {
  const { useSyncExternalStore } = await import('react')
  return {
    useRouter: () => ({ push: vi.fn(), replace: nav.replace, prefetch: vi.fn(), back: vi.fn() }),
    usePathname: () =>
      useSyncExternalStore(
        (listener: () => void) => {
          nav.listeners.add(listener)
          return () => nav.listeners.delete(listener)
        },
        () => nav.state.pathname,
      ),
    useParams: () => ({}),
    useSearchParams: () => new URLSearchParams(),
  }
})

vi.mock('@/lib/api/organizations', () => ({
  meOrganizationsApi: { list: vi.fn(), setActive: vi.fn() },
  tenantOrganizationsApi: {},
}))

const PREV_ORG = 'org-prev-7f3a'
const NEXT_ORG = 'org-next-91bc'
const PREV_IDS = [PREV_ORG, 'agent-prev-1', 'conv-prev-1', 'artifact-prev-1']

const ALL_STORE_MODULES = [
  chatArtifactsStore,
  chatDataUiStore,
  chatFollowupStore,
  chatMemoryRecallStore,
  chatNavigatorStore,
  chatRightRailStore,
  chatSkillBuilderStore,
  chatStore,
  chatSubagentNamesStore,
  sidebarStore,
]

type JotaiStore = ReturnType<typeof useStore>
type ReadableAtom = Parameters<JotaiStore['get']>[0]

function isAtom(value: unknown): value is ReadableAtom {
  return (
    typeof value === 'object' &&
    value !== null &&
    'read' in value &&
    typeof (value as { read: unknown }).read === 'function'
  )
}

/** Values of every atom exported from `src/lib/stores/*` in the given store. */
function snapshotJotai(store: JotaiStore): string {
  const values: unknown[] = []
  for (const mod of ALL_STORE_MODULES) {
    for (const [name, value] of Object.entries(mod)) {
      if (!isAtom(value)) continue
      try {
        values.push([name, store.get(value)])
      } catch {
        // write-only / derived atoms that need context are not state holders
      }
    }
  }
  return JSON.stringify(values)
}

function snapshotStorage(): string {
  const dump = (storage: Storage) =>
    Array.from({ length: storage.length }, (_, i) => {
      const key = storage.key(i) ?? ''
      return `${key}=${storage.getItem(key)}`
    })
  return JSON.stringify([dump(window.localStorage), dump(window.sessionStorage)])
}

/**
 * The org list itself legitimately names every org the user belongs to (the
 * switcher must list them); every other cached query must be free of the
 * previous org's ids.
 */
function snapshotQueryCache(queryClient: QueryClient): string {
  return JSON.stringify(
    queryClient
      .getQueryCache()
      .getAll()
      .filter((q) => q.queryKey.join('/') !== organizationQueryKeys.mine.join('/'))
      .map((q) => [q.queryKey, q.state.data]),
  )
}

function expectNoPreviousIds(snapshot: string, surface: string) {
  for (const id of PREV_IDS) {
    expect({ surface, found: snapshot.includes(id) }).toEqual({ surface, found: false })
  }
}

let activeOrg = PREV_ORG
let capturedStore: JotaiStore | null = null
let streamSignal: AbortSignal | null = null

function OrgScopedScreen() {
  const store = useStore()
  useEffect(() => {
    capturedStore = store
  }, [store])
  const agents = useQuery({
    queryKey: ['agents'],
    queryFn: async () =>
      activeOrg === PREV_ORG
        ? [{ id: 'agent-prev-1', org_id: PREV_ORG }]
        : [{ id: 'agent-next-1', org_id: NEXT_ORG }],
  })
  // Route-id-keyed queries (one polling) like the chat screen's.
  const conversationId = usePathname().match(/conversations\/([^/]+)/)?.[1] ?? null
  const messages = useQuery({
    queryKey: ['conversations', conversationId, 'messages'],
    queryFn: async () => [{ id: `msg-of-${conversationId}` }],
    enabled: conversationId !== null,
  })
  useQuery({
    queryKey: ['conversations', conversationId, 'active-run'],
    queryFn: async () => ({ conversation_id: conversationId, status: 'running' }),
    enabled: conversationId !== null,
    refetchInterval: 5,
  })
  const [rail, setRail] = useAtom(chatRightRailStore.chatRightRailAtom)
  const setExpanded = useSetAtom(chatNavigatorStore.expandedAgentIdsAtom)
  const setRuntime = useSetAtom(chatNavigatorStore.conversationRuntimeStatusAtom)
  const setFollowup = useSetAtom(chatFollowupStore.setConversationFollowupAtom)
  const [draft, setDraft] = useState('')
  useRegisterUnsavedOrgDraft(draft !== '')

  return (
    <div>
      {agents.data?.map((agent) => (
        <a key={agent.id} data-agent-id={agent.id} href={`/agents/${agent.id}`}>
          {agent.org_id}
        </a>
      ))}
      {messages.data?.map((m) => (
        <span key={m.id}>{m.id}</span>
      ))}
      <span data-testid="rail">{JSON.stringify(rail)}</span>
      <span data-testid="draft">{draft}</span>
      <button
        type="button"
        onClick={() => {
          setRail({ mode: 'artifacts', artifacts: { conversationId: 'conv-prev-1', view: 'list' } })
          setExpanded(['agent-prev-1'])
          setRuntime({ 'conv-prev-1': 'running' })
          setFollowup({ conversationId: 'conv-prev-1', suggestion: 'artifact-prev-1' })
          storeFixInitialMessage(`fix agent-prev-1 in ${PREV_ORG}`)
          void apiFetch('/api/conversations/conv-prev-1/stream').catch(() => undefined)
        }}
      >
        seed
      </button>
      <button type="button" onClick={() => setDraft(`draft for ${PREV_ORG}`)}>
        edit
      </button>
    </div>
  )
}

function SwitchButton() {
  const { switchOrganization } = useOrgContext()
  return (
    <button
      type="button"
      onClick={() => {
        void switchOrganization(NEXT_ORG).catch(() => undefined)
      }}
    >
      switch
    </button>
  )
}

function setup() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <NextIntlClientProvider locale="ko" messages={messages}>
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      </NextIntlClientProvider>
    )
  }
  const view = render(
    <Wrapper>
      <OrgContextProvider>
        <SwitchButton />
        <OrgScopedScreen />
      </OrgContextProvider>
    </Wrapper>,
  )
  return { queryClient, view }
}

const ORGS_BEFORE = [
  makeOrganization({ id: PREV_ORG, name: 'Prev', is_active: true }),
  makeOrganization({ id: NEXT_ORG, name: 'Next', is_active: false, is_default: false }),
]

describe('organization switch reset (B-09)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    activeOrg = PREV_ORG
    capturedStore = null
    streamSignal = null
    window.localStorage.clear()
    window.sessionStorage.clear()
    nav.state.pathname = '/agents/agent-prev-1/conversations/conv-prev-1'
    nav.state.duringReplace = null
    vi.mocked(meOrganizationsApi.list).mockImplementation(async () =>
      ORGS_BEFORE.map((org) => ({ ...org, is_active: org.id === activeOrg })),
    )
    vi.mocked(meOrganizationsApi.setActive).mockImplementation(async (orgId: string) => {
      activeOrg = orgId
      return { org_id: orgId, tenant_id: 'tenant-1' }
    })
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          streamSignal = init?.signal ?? null
          init?.signal?.addEventListener('abort', () => reject(init.signal?.reason))
        }),
    )
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('leaves zero previous-org ids in DOM, query cache, Jotai store and web storage', async () => {
    const { queryClient, view } = setup()
    await screen.findByText(PREV_ORG)
    await screen.findByText('msg-of-conv-prev-1')
    await userEvent.click(screen.getByRole('button', { name: 'seed' }))
    // Unobserved cache entries (closed screens) must be dropped, not refetched.
    queryClient.setQueryData(['artifacts', 'artifact-prev-1'], { id: 'artifact-prev-1' })
    queryClient.setQueryData(organizationQueryKeys.org(PREV_ORG), { id: PREV_ORG })

    // Sanity: the previous org's ids really are present on every surface first.
    expect(capturedStore).not.toBeNull()
    expect(view.container.innerHTML).toContain('conv-prev-1')
    expect(snapshotQueryCache(queryClient)).toContain('agent-prev-1')
    expect(snapshotJotai(capturedStore as JotaiStore)).toContain('conv-prev-1')
    expect(snapshotStorage()).toContain('agent-prev-1')
    expect(snapshotStorage()).toContain(PREV_ORG)
    expect(streamSignal?.aborted).toBe(false)

    // A request issued during the switch window (after unmount, before the
    // new org mounts) must get an already-aborted signal.
    let signalDuringSwitch: AbortSignal | undefined
    let screenMountedDuringSwitch = true
    nav.state.duringReplace = () => {
      signalDuringSwitch = withOrgScopeSignal('/api/conversations/conv-prev-1/messages')
      screenMountedDuringSwitch = screen.queryByText(PREV_ORG) !== null
    }

    await userEvent.click(screen.getByRole('button', { name: 'switch' }))
    await screen.findByText(NEXT_ORG)

    expect(meOrganizationsApi.setActive).toHaveBeenCalledWith(NEXT_ORG)
    expect(streamSignal?.aborted).toBe(true)
    expect(nav.replace).toHaveBeenCalledWith('/')
    expect(signalDuringSwitch?.aborted).toBe(true)
    expect(screenMountedDuringSwitch).toBe(false)
    // After the switch, requests run again under the new generation.
    expect(withOrgScopeSignal('/api/agents')?.aborted).toBe(false)
    // Atoms backed by web storage re-read it in the new store: empty.
    expect(capturedStore?.get(chatNavigatorStore.expandedAgentIdsAtom)).toEqual([])

    // Let the polling query tick: it must not re-create a previous-route key.
    await new Promise((resolve) => setTimeout(resolve, 30))

    expectNoPreviousIds(view.container.innerHTML, 'dom')
    expectNoPreviousIds(snapshotQueryCache(queryClient), 'query cache')
    expectNoPreviousIds(snapshotJotai(capturedStore as JotaiStore), 'jotai store')
    expectNoPreviousIds(snapshotStorage(), 'web storage')
  })

  it('falls back to a hard navigation when the router never reaches the landing path', async () => {
    const assign = vi.fn()
    vi.spyOn(window, 'location', 'get').mockReturnValue({
      ...window.location,
      assign,
    } as Location)
    // The router ignores the replace: pathname stays on the previous route.
    nav.replace.mockImplementationOnce(() => undefined)
    setup()
    await screen.findByText(PREV_ORG)
    vi.useFakeTimers()
    try {
      fireEvent.click(screen.getByRole('button', { name: 'switch' }))
      // Flush the mutation and the purge (microtasks only, no timer time).
      for (let i = 0; i < 10; i += 1) await act(() => vi.advanceTimersByTimeAsync(0))
      expect(screen.getByTestId('org-switching')).toBeInTheDocument()
      act(() => vi.advanceTimersByTime(LANDING_TIMEOUT_MS - 1))
      expect(assign).not.toHaveBeenCalled()
      act(() => vi.advanceTimersByTime(1))
      expect(assign).toHaveBeenCalledWith('/')
    } finally {
      vi.useRealTimers()
      act(() => finishOrgSwitch())
    }
  })

  it('keeps everything when the user declines to discard an unsaved draft', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    setup()
    await screen.findByText(PREV_ORG)
    await userEvent.click(screen.getByRole('button', { name: 'edit' }))
    await userEvent.click(screen.getByRole('button', { name: 'switch' }))

    expect(confirmSpy).toHaveBeenCalled()
    expect(meOrganizationsApi.setActive).not.toHaveBeenCalled()
    expect(screen.getByTestId('draft')).toHaveTextContent(PREV_ORG)
  })

  it('a refused switch keeps the previous organization state', async () => {
    vi.mocked(meOrganizationsApi.setActive).mockRejectedValue(new Error('forbidden'))
    const { queryClient } = setup()
    await screen.findByText(PREV_ORG)
    await userEvent.click(screen.getByRole('button', { name: 'switch' }))

    await waitFor(() => expect(meOrganizationsApi.setActive).toHaveBeenCalled())
    expect(screen.getByText(PREV_ORG)).toBeInTheDocument()
    expect(snapshotQueryCache(queryClient)).toContain('agent-prev-1')
    expect(nav.replace).not.toHaveBeenCalled()
  })
})

describe('org-scoped request signal', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('gates org-scoped requests during a switch; auth and org navigation stay linked-free', async () => {
    const { beginOrgSwitch, finishOrgSwitch, currentOrgScope, signalForOrgScope } =
      await import('@/lib/org-scope/org-scope-signal')
    const scoped = withOrgScopeSignal('/api/agents')
    const token = currentOrgScope()
    expect(withOrgScopeSignal('/api/auth/me')).toBeUndefined()
    expect(withOrgScopeSignal('http://localhost:8001/api/me/organizations')).toBeUndefined()

    act(() => beginOrgSwitch())
    expect(scoped?.aborted).toBe(true)
    expect(withOrgScopeSignal('/api/agents')?.aborted).toBe(true)
    act(() => finishOrgSwitch())
    expect(withOrgScopeSignal('/api/agents')?.aborted).toBe(false)
    // A token from the previous generation stays aborted.
    expect(signalForOrgScope(token, '/api/agents')?.aborted).toBe(true)
  })

  it('a LangGraph transport from the previous org never reopens its event stream', async () => {
    const { beginOrgSwitch, finishOrgSwitch } = await import('@/lib/org-scope/org-scope-signal')
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(() => new Promise<Response>(() => undefined))
    type Params = Parameters<ReturnType<typeof createMoldyAgentTransport>['openEventStream']>[0]
    const params = { channels: ['values'] } as unknown as Params

    const current = createMoldyAgentTransport('conv-now-1', 'agent-now-1')
    current.openEventStream(params)
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled())

    const stale = createMoldyAgentTransport('conv-prev-1', 'agent-prev-1')
    act(() => beginOrgSwitch())
    act(() => finishOrgSwitch())
    fetchSpy.mockClear()
    const handle = stale.openEventStream(params)
    await handle.ready
    const events: unknown[] = []
    for await (const event of handle.events) events.push(event)
    expect(events).toEqual([])
    await new Promise((resolve) => setTimeout(resolve, 10))
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('the AbortSignal.any fallback detaches its listeners once settled', async () => {
    const { anySignal } = await import('@/lib/org-scope/org-scope-signal')
    const a = new AbortController()
    const b = new AbortController()
    const removeSpy = vi.spyOn(b.signal, 'removeEventListener')
    const combined = anySignal([a.signal, b.signal], false)
    a.abort('first')
    expect(combined.aborted).toBe(true)
    expect(combined.reason).toBe('first')
    expect(removeSpy).toHaveBeenCalledWith('abort', expect.any(Function))
  })
})

describe('orgSwitchLandingPath', () => {
  it('keeps only role-independent settings pages', async () => {
    const { orgSwitchLandingPath } = await import('@/lib/providers/org-context-provider')
    expect(orgSwitchLandingPath('/settings/appearance')).toBe('/settings/appearance')
    expect(orgSwitchLandingPath('/settings/security')).toBe('/settings/security')
    expect(orgSwitchLandingPath('/settings/memory')).toBe('/settings/memory')
    expect(orgSwitchLandingPath('/settings/organization/members')).toBe('/')
    expect(orgSwitchLandingPath('/settings/credentials')).toBe('/')
    expect(orgSwitchLandingPath('/agents/agent-prev-1/conversations/conv-prev-1')).toBe('/')
    expect(orgSwitchLandingPath(null)).toBe('/')
  })
})
