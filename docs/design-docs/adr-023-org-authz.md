# ADR-023: Tenant, organization, authorization and sharing

Date: 2026-10-10
Status: Accepted for implementation; production enforcement pending evidence
Plan-Task: A-01

## Context

The implementation baseline is `9657ca60552e8f5e8c45ce6a8bd176c500c8992a`.
Existing JWT authentication establishes an active user, while services authorize
primarily by ownership and the platform operator flag. Conversations inherit
ownership from agents. There is no company or organization boundary. Sharing
therefore requires changes to persistence, runtime credential resolution,
background work, public links, UI state and authorization, together.

The user authorized the complete implementation in
[the supplied plan](../exec-plans/active/org-authz-plan.html), including its chapter 19
decisions. The original HTML is preserved. Deviations and verification evidence
are recorded in [the execution record](../exec-plans/active/org-authz-progress.md).

## Decisions

1. **D1–D4:** OpenFGA server 1.22.0, Python SDK 0.10.5, one store per instance.
   PostgreSQL owns relationships; OpenFGA is a rebuildable projection. Each
   organization belongs to one tenant and each shared resource to one
   organization. The application rejects cross-organization grants before writes.
2. **D5–D7:** Private data uses user and organization filters outside OpenFGA.
   The application checks action permissions, not owner/editor roles. Common
   sharing levels are consumer, viewer, editor, manager, owner; credentials
   expose consumer, manager and owner only.
3. **D8–D9:** Administrators govern resources without implicit content access.
   Platform operators cannot read tenant content. Single-tenant migration also
   grants the existing operator tenant and default-organization administration.
4. **D10–D13:** Current organization is a server-set HttpOnly cookie with
   membership verified on every request. Rollout is legacy, shadow, enforce,
   optionally by type. Additions commit DB and outbox before FGA writes.
   Revocations commit a denial marker first, then delete FGA tuples, then
   finalize. Lists combine DB candidates and batches of 50 checks.
5. **D14–D18:** Resource attachments record the delegating user and source link.
   Loss of the delegator's rights breaks the attachment. Credentials have
   none/shared/per_user modes; personal accounts cannot be shared by default.
   Credential-bearing delegation requires can_share. Authentication interrupts
   use the existing durable HITL path, with server validation of resume choices.
   Credential destinations are restricted by allowed hosts, including redirects.
6. **D19–D20:** Agent deletion archives the agent, immediately stopping execution,
   sharing and schedules. Permanent deletion preserves private conversations as
   read-only records with agent-name snapshots. All 13 runtime construction paths
   pass through prepare_authorized_runtime.
7. **D21–D25:** Tenant is the company boundary, organization the sharing boundary.
   Deployment mode is single_tenant or multi_tenant. Platform, tenant and
   organization settings only narrow permission. Transaction-scoped tenant RLS
   provides defense in depth. Limits apply simultaneously to company,
   organization, user and agent.

## Confirmed product choices (chapter 19)

- Users may belong to multiple organizations and tenants. Upgraded users are
  builders; newly registered users are members. Auditors need no content access.
- Personal account definitions are google_workspace_oauth2, srt_account,
  ktx_account, foresttrip_account and, by default, mcp_oauth2.
- Runtime memory is user plus user×agent; shared operational memory is edited
  only from settings by editors. Other users submit proposals.
- Removed-member retention defaults to 30 days (7–3650); agent trash to 30 days
  (7–90); conversation retention is unlimited by default (30–3650 when set).
- Marketplace defaults to organization visibility. Tenant publication policy is
  disabled/open/approval, default approval. Installations are credential-free copies.
- Each tenant has envelope-encrypted data keys; old ciphertext stays readable
  during migration. Tenant audit retention defaults to 365 days (90–3650).
- This scope includes verified domains, tested per-tenant OIDC and LDAP/AD,
  required SSO, emergency administrators, JIT and IdP-group role mappings.
  SAML uses optional Keycloak mediation.
- Agent runtime identity and delegated human identity are recorded separately.
- Follow-ups remain outside scope: email delivery, self-service company signup,
  compliance conversation access, direct SAML, SCIM, customer-managed keys,
  external agent identities/token exchange, billing and organization-crossing sharing.

## Rollout and acceptance

PR 1 preserves AUTHZ_MODE=legacy and DB_RLS_ENABLED=false. It includes P0–P7
and P8 implementation tasks. Production shadow observation requires 14
consecutive days of zero differences with real requests. Enforcement then
stabilizes for two weeks before PR 2 removes legacy code and destructive columns.
These elapsed-time operational gates cannot be replaced by synthetic tests.
User study U-05 requires real internal participants; automated browser checks
are separate evidence.

Every task has an atomic commit, tests and explicit acceptance status. New routes
must declare a policy. Cookie mutations retain CSRF. Hidden resources respond
404; a visible resource with insufficient action permission responds 403.
Enforced FGA errors deny with 503, while private reads remain independent.

## Decision clarifications

- A-13's [event and schema crosswalk](../org-authz/org-authz-event-contract.md)
  records all 19 original event rows, their concrete DB/tuple/revocation sources,
  and explicit R1–R8 resolutions where the original sections conflict. The
  crosswalk is a reviewed schema contract; event execution and exact emitted
  tuple-set tests remain requirements of the later service/runtime tasks.
- m82 records its original and completed modified values in the reserved,
  server-managed `tenants.settings._migration_m82` entry. Rollback verifies the
  complete snapshot and dependency inventory before writes; new rows, changed
  ownership/scope or missing provenance require explicit reconciliation. It
  never clears ownership from post-upgrade conversations. Subsequent tenant
  settings APIs must reject client changes to reserved keys and preserve them
  during settings updates. The snapshot excludes credential ciphertext/password
  fields, is not an authorization receipt, and must not be exposed through the
  product settings response.
  Snapshot version 2 also records actual incoming-FK dependency closure and
  optional saver identity/reference keys. New or reparented indirect rows must
  fail preflight before parent ownership or scope is restored; payload and
  credential secret fields are excluded. Missing version-2 provenance requires
  reconciliation. PostgreSQL locks keep verification and restoration in one
  writer-stable transaction; the concurrency regression uses a real second
  connection during that window.
- Deferred m92 explicitly classifies every physical table, including indirect
  child/history rows and the four LangGraph saver tables. Unknown tables stop
  activation before policy DDL. Parent EXISTS policies protect child rows and
  secondary references; foreign keys alone do not propagate RLS. Global identity
  gateways use a trusted internal identity context. Outbox, migration history
  and saver tables use a trusted internal platform service context; neither
  context may come from HTTP input or `is_super_user`. Tenant sessions cannot
  read saver payloads. The future saver pool must enter its service context only
  after conversation/runtime authorization. This strengthens the plan's
  checkpointer exception; it does not enable RLS in the current app.
- A-13 makes `user_identities.connection_id` nullable with `ON DELETE SET NULL`.
  It records the connection that first established the global identity, rather
  than owning that identity. Every login must independently validate its current
  active tenant connection, verified issuer, subject and tenant membership; it
  must never authorize through the historical connection pointer. Removing or
  disabling tenant A's connection cannot affect tenant B's mapping for the same
  OIDC issuer and subject. LDAP issuers are namespaced by connection ID because
  directory subject identifiers are connection-local. This retains the plan's
  22 tables and global unique issuer/subject contract without tenant-owned global
  identity lifecycle. H-08 must test current-connection acceptance end to end.
- LDAP settings have explicit encrypted bind-password, key ID, server/TLS,
  user/group base DN and filters, attribute mapping and CA certificate columns.
  Draft connections may be incomplete; tested/active LDAP connections require
  their mandatory settings. The database rejects plaintext LDAP URLs; runtime
  H-08 still must validate certificates, perform StartTLS before credentials and
  suppress credential logging.
- A-13 mentions 19 new tables, while sections 6.1 and 20.1 specify 22 including
  three SSO tables. The canonical schema scope is all 22 tables, not 19.
- Section 18 suggests a separate D-01 PR, conflicting with the detailed two-PR
  delivery in 17.4. Follow 17.4: D-01 remains an independently reviewable commit
  inside PR 1.
- Reserved knowledge/database/workflow types remain model-only until their
  features exist. No placeholder product endpoints are added for them.
- A-08 uses a syntax-aware inventory of 173 occurrences in 65 files, verified
  against the exact baseline commit. Appendix A's restricted regex omits 19
  occurrences in eight files and undercounts 10 occurrences in listed files.
  The stronger ratchet pins expressions and occurrence counts per file, so
  deleting one old predicate cannot excuse adding a different one. Historical
  HTML totals remain unchanged.
- A-07 initially attaches policies from frozen, explicit route identities at app
  registration instead of editing 49 existing route modules solely for markers.
  Unknown routes receive no default and fail the meta test. P3 declarations take
  precedence; this reduces unrelated route churn while preserving the gate.
- A-09 pins CSRF presence during the legacy phase. The claimed existing
  ownership-before-CSRF order is false: E2E helpers explicitly put ownership
  after CSRF. The stricter target order will be introduced and behavior-tested
  with resource policies in P3; legacy response precedence remains unchanged.
- A-10 confirms that LangChain after-model hooks run in reverse middleware
  order. P5 must assemble `[approval, connection_guard]` with `interrupt_on=None`
  explicitly, so the guard node executes before the approval node. Combining
  both interrupts inside one hook is unsafe: after connection state changes,
  the approval interrupt can consume the earlier authentication resume value.
  Keep separate checkpoint nodes and use the native HITL `when` predicate to
  exclude tool calls already resolved by permission-denial ToolMessages. Cancel
  must resolve outstanding call IDs before ending the graph. This narrows the
  existing ADR-012 top-level injection rule for the authorized runtime path.
- A-05 requires the model-version schema introduced by m84 in P2. P0 tests use
  its explicit Core table contract in owned disposable schemas, including real
  PostgreSQL. The CLI never creates tables or migrates an installation. Actual
  installation bootstrap therefore follows m84; P0 validates the command first.
  `MIGRATION_DATABASE_URL` is mandatory for bootstrap, and read commands require
  explicit immutable store/model IDs rather than silently using the latest model.
- A-13 keeps drafts outside the live Alembic versions until their task stage is
  approved. The active head remains m77 during P0; schema review uses a separate
  declarative metadata rather than registering unfinished models in the app.
  All 22 new definitions are present, including the SSO trio that section 6.3
  omitted. Their creation is included in m80, before scope/backfill work.
- The chain is m78–m89, then m92, m90, m91. Consecutive numeric ordering would
  require PR2's legacy deletion before PR1/I-06 RLS and the enforcement
  stabilization period. Preserve revision IDs but correct dependencies; keep
  m92 operationally deferred and m90/m91 on PR2. No automatic `upgrade head`
  crosses deferred activation. Deferred drafts require observation/backup
  receipts; the explicit disposable-validation option is test evidence only.
- Physical clean-m77 PostgreSQL contains five additional historical tables:
  `_m11_dedup_connection_snapshot`, `_m11_dedup_tool_remap`,
  `_m11_tool_backfill_provenance`, `_m9_migrated_connections`, and
  `agent_creation_sessions`. None is read by app code. They retain old migration
  provenance/compatibility data and are preserved. Report 63 active ORM tables
  separately from 68 physical business/history tables; at m89 these become
  85 active and 90 physical, excluding Alembic and runtime-managed saver tables.
  Deferred RLS restricts the historical tables and global projection tables to
  explicit platform scope, rather than exposing them in tenant sessions.
- Existing FK/unique names differ between ORM create_all and migrated
  PostgreSQL. Drafts discover and replace the exact source constraint by its
  columns and fail on ambiguity. Rollback refuses preserved orphan histories or
  spend keys that cannot be collapsed without losing data.
- B-03 follows section 16.6 for explicit invalid organization cookies: deny with
  403 rather than silently selecting another organization. Section 8.1's
  illustrative fallback applies only to a stale saved preference when the
  cookie is absent. Both company and organization membership must be active,
  including for platform operators. Keep `OrgContext` separate from
  `CurrentUser`; no organization/authority claim is added to JWT. Read membership
  from DB on every request until a mutation-aware process cache exists; such a
  cache must preserve immediate removal/suspension behavior. Self organization
  listing/switching is not resource permission enforcement.

## B11/B12 company administration (candidate - verification pending)

This section records the design of the B11 company administration API and the
B12 SaaS operator API as implemented on `feature/org-authz` after the B08
baseline `c5cea625ee1e1470e386525edf120b02178bdbaf`. It describes a candidate.
The aggregate gate (parent Task 16), the F1–F6 reviews and the native
PostgreSQL lanes have not run, so nothing here records adoption of B11 or B12.
The route-by-route contract and the 9.7 ownership crosswalk are in
[the company administration contract](../org-authz/org-authz-company-administration-contract.md).
The candidate state is in [the execution record](../exec-plans/active/org-authz-progress.md).

### Schema: a dedicated company invitation table

- Migration `b11_company_administration` has exactly one parent,
  `b08_agent_lifecycle`. It adds `tenant_invitations`,
  `organizations.archived_at` and the `conversation_runs` materialization
  pair. It does not reuse `org_invitations` with a nullable `org_id`, for three
  reasons. First, a company administrator need not belong to any organization.
  Second, a nullable scope would weaken the organization FK and pending-row
  invariants that every B05 organization-invitation consumer relies on, and
  each of those consumers would need a null branch. Third, a company invitation
  grants no organization membership, so its acceptance rules differ.
- `tenant_invitations` stores a normalized (`lower(trim())`) email, a role of
  NULL, `admin` or `auditor`, a status of `pending`, `accepted` or `revoked`
  (a check constraint enforces the matching timestamp state), a unique token
  hash of exactly 64 lowercase hex digits, `expires_at > created_at`,
  actor/timestamp pairs, and a partial index on pending
  `(tenant_id, email, role)`.
- A check constraint ties `organizations.archived_at` to
  `status = 'archived'`. The migration backfills legacy archived rows from
  `updated_at`.
- `conversation_runs.runtime_materialized_at` and
  `runtime_materialization_sha256` are both set or both NULL, and the digest is
  64-hex. Together they are the durable cross-process marker of a prepared run.

### Token purpose and redaction

- A company token has the form `tv1.<invitation id hex>.<HMAC-SHA256>`. The
  MAC key is the JWT secret and the message is
  `"moldy/tenant-invitation/v1\0" || invitation_id.bytes`. Because the purpose
  string is separate from organization invitation tokens, one token cannot be
  replayed as the other. `POST /api/invitations/accept` and
  `POST /api/auth/register` select the company path by the `tv1.` prefix and
  use the B05 organization path otherwise.
- Only the SHA-256 of the token is stored. Tokens last seven days and can be
  used once. Because the plaintext can be rebuilt from the row ID, an
  idempotent create retry can return the same link. If the JWT secret has been
  rotated, the rebuilt token no longer matches the stored hash, and the
  response carries `token: null` until someone resends the invitation. Resend
  revokes the original and any same-content pending rows, then creates a
  replacement row with a new ID, so the token is new too.
- The plaintext link appears only in the create and resend responses and in
  the B12 company-creation response (for the first administrator). Lists,
  revocations and every other view omit both the token and its hash. Member
  views expose only a safe company identity and status. Auditor views expose
  counts and organization `name`/`member_count`. Administrator views add
  management metadata. Every view omits the secrets namespace, raw settings,
  key material, catalog, usage, audit and content IDs.

### If-Match

The Tenant ETag is the quoted ISO-8601 UTC value of `tenants.updated_at`. Each
mutation advances that value by at least 1 µs, so two writes in the same clock
tick still get different ETags. The Organization ETag is the quoted ISO-8601
UTC value of `organizations.updated_at`. A missing, malformed or stale value
returns `409 stale_version`.

| ETag | Routes |
| --- | --- |
| Tenant | B11 `PATCH /api/tenants/{tenant_id}`; members POST, PATCH and DELETE; `PUT .../roles/{user_id}`; invitations POST, resend and DELETE. B12 `PATCH /api/admin/tenants/{tenant_id}`, suspend and resume |
| Organization | `PATCH .../organizations/{org_id}` (rename), archive, restore |
| None | `POST .../organizations` (create), `POST /api/invitations/accept`, `POST /api/auth/register`, B12 `POST /api/admin/tenants` |

Precedence follows the contract. Invitation create checks ETag syntax first.
A same-content pending retry then returns `200` before the staleness
comparison, so a retry that sends a well-formed but stale ETag still gets the
idempotent result. Archiving a default organization returns `409` before the
ETag check. Suspend and resume take the Tenant lock, then check If-Match, then
treat a repeat of the current state as a no-op that changes neither the
version nor the audit log.

### Membership lock order and default registration

- Every membership-derived write and every invitation acceptance follows the
  same order. It first locks `Tenant` by primary key, sorting several tenants
  by UUID bytes. It then locks every affected existing `Organization` in
  ascending UUID-byte order. Only then does it lock the child rows
  (invitation, `TenantMember`, `OrganizationMember`, role, group and
  capability rows, envelopes) in stable table/key order. Under those locks it
  checks authority, target eligibility, membership state and preconditions
  again. Router and dependency reads, and invitation hash hints, are
  non-authoritative. No path locks an Organization before its Tenant
  (`app/authz/tenant_org_write_locks.py`).
- Public default registration is fixed to the bootstrap install company
  `DEFAULT_COMPANY_ID = uuid5(NAMESPACE_URL, "moldy/default-tenant")` and its
  `is_default` organization. A `default` organization that an operator creates
  for a new company is never a public default-registration target.
  Company-token registration creates only a tenant membership with an
  optional role. It enrols in no organization and sets neither
  `last_active_org_id` nor the active-organization cookie. When
  `allow_first_user_as_admin` is set, it also locks the fixed default Tenant,
  in sorted order, to serialize the first-user check.
- Default-organization archive policy (Policy A): no default organization can
  be archived. That includes a legacy default that is already archived. Once
  authority and the locks have been resolved without revealing whether the
  organization exists, a locked `is_default` row returns
  `409 default_organization_archive_forbidden`. This happens before the
  stale-version, confirmation-name and last-organization checks, and changes
  neither state nor the audit log. B11 never reassigns or clears `is_default`.
  Policy A narrows the canonical plan, which only forbids archiving the last
  organization (9.7 row at `org-authz-plan.html:1217`, S05 at line 1989). The
  narrowing is a decision of the parent plan
  (`.omo/plans/p1-company-administration.md:13`), not a chapter 19 decision of
  the HTML plan. Because the first organization of an operator-created company
  is its default organization, that organization can never be archived.

### Organization administrators, roles and exit

- `OrganizationCreate.administrator` may name an active tenant member, or an
  email that receives an organization invitation. After creation,
  administrators are managed only through the existing B04 routes
  `POST /api/orgs/{org_id}/members` and `PUT /api/orgs/{org_id}/roles`, which
  keep the Organization ETag and the last-administrator guard. B11 adds no
  duplicate administrator route.
- `PUT /api/tenants/{tenant_id}/roles/{user_id}` replaces the whole set of
  tenant roles. `{"roles": []}` is valid and leaves an active plain member. The
  final active company administrator cannot be role-cleared, suspended or
  removed. A tenant role never grants organization or content access.
- Exit seam: organization removal is split into the transaction-neutral
  functions `preflight_member_exit` and `apply_member_exit`. Company removal
  locks the Tenant and every affected Organization, preflights every
  membership, and then, in one caller transaction, applies the per-organization
  effects, the tenant-role deletion and `TenantMember.status = removed`.
  Covered owned resources abort with `409 resource_transfer_required`. Nothing
  is transferred automatically. E02 (transfer) and C04 (grant outbox) are the
  registered extension owners of this seam.

### Identifiers

- Tenant slug: supplied by the operator, globally unique (`uq_tenants_slug`),
  lowercase ASCII alphanumerics and hyphens, 3–40 characters, starting and
  ending with an alphanumeric. The secrets namespace is
  `tenants/{tenant_id}` (unique) and is never derived from the slug.
- Organization slug: derived on the server from the name by `slugify` (falling
  back to `org`), cut to 120 characters, and made unique within the tenant by
  `-2`, `-3`, … suffixes. Renaming keeps the slug. Names need not be unique.
  The first organization of an operator-created company has the slug
  `default`. The two algorithms are separate on purpose: a tenant slug is an
  external identifier, while an organization slug is tenant-local.

### Prepared runs and archive

- `PreparedRunLease` (`app/agent_runtime/runtime_config.py`) belongs to one
  process. It holds the compiled root and child runtimes, converted messages,
  the LangGraph config, the lease-bound checkpointer and seed, immutable
  snapshots, tool access classes and resolved secret values. Pickling or
  copying it raises an error. Only its non-secret digest is persisted, together
  with `runtime_materialized_at` and the `queued -> running` transition, while
  the materialization locks are held. The process-local registry moves a lease
  through `reserved -> pending_commit -> active [-> dispatching] ->
  terminalizing -> removed` and retires removed run IDs, so a lost lease is
  never rebuilt.
- After the marker, the access contract is closed:
  - The checkpoint saver reads only the frozen seed and the checkpoints this
    lease wrote under the exact thread, namespace and `moldy_run_id`. Any
    other access fails with `checkpoint_lineage_denied`.
  - Every tool belongs to exactly one class: `snapshot_safe`,
    `resolved_external`, `run_output` or `live_application_resource`.
    Classification is closed, nothing defaults to `snapshot_safe`, and live
    resources are limited to IDs preloaded into the lease.
  - The snapshot backend serves sealed snapshot paths and writes only below
    run-owned roots, which are kept separate from the shared data root.
  - Preparation, MCP discovery, credential resolution, memory loading, global
    checkpoint access and initial history loading all fail with
    `post_marker_hydration_denied`.
- Every execution entry (chat, queue/worker, Agent API, trigger, subagent)
  goes through `organization_execution_gateway`. The gateway locks
  `Tenant -> Organization -> owner TenantMember -> owner OrganizationMember ->
  User (KEY SHARE) -> sorted Agent lifecycle -> sorted Conversation` and
  requires an active Tenant, both owner memberships active, and an active user.
  An archived Organization is admitted only for the exact live lease. The
  gateway runs whether or not the lease flag is set, so legacy runs now also
  fail closed on suspension, owner-membership loss or archive.
- The lock order that covers all entries and organization archive is
  `Tenant, Organization, TenantMember, OrganizationMember, User, Agent
  lifecycle, AgentTrigger, AgentDeployment, Conversation, Run, Input,
  Envelope, Queue` (`ExecutionLockCursor` admits only ascending positions).
  Agent locks come before Trigger and Deployment locks, which matches the
  source order of the standalone B08 agent archive, restore and purge
  (`Agent lifecycle -> Agent -> AgentTrigger -> AgentDeployment`). Those B08
  paths take no parent row after Agent. The archive takes the Organization
  row `FOR UPDATE` before the Agent lifecycle lock, because it revokes the
  removed subagent links' delegations; taking it during the revoke inverted
  the order and deadlocked with queue recovery. A trigger claim enters the gateway and
  the Agent tree before it locks the trigger.
- Terminal states. Organization archive, and the reusable `tenant_suspended`
  writer seam, apply one projection to rows that are already locked:
  - an unclaimed pending input becomes `canceled`, keeps `run_id = NULL`, and
    its revision increases by 1;
  - a claimed input stays bound and becomes `failed`;
  - a run without the materialization pair becomes `failed`, inactive, with
    `completed_at` set and the exact `error_code`/`error_message`;
  - a linked API run becomes `failed` with `finished_at`, the same code and its
    link kept;
  - a linked trigger run becomes `failed` with `error_message = reason`;
  - live API and trigger envelopes that never linked to a run fail with the
    same code.

  Active triggers are paused (`next_run_at` cleared) and active deployments
  are disabled. Agent rows are never archived. A run that carries the
  materialization pair is left to finish through the closed contract. If its
  lease is lost, every dispatch fails with `prepared_run_lease_missing` and
  recovery terminalizes it (`running` becomes `failed`, `canceling` becomes
  `canceled`) without reading the agent or config and without rebuilding.
  Recovery under a denied gateway uses narrow terminal-only locks. A single
  table (`app/agent_runtime/run_error_codes.py`) maps errors to run codes:
  `tenant_suspended`, `forbidden`, `organization_archived`,
  `prepared_run_lease_missing` and `prepared_run_start_failed`. Nothing is
  retried or replayed automatically. Unpaired runs remain in ordinary queue
  recovery, which may prepare them again.
- Restore changes metadata only. It requires an archived row less than 30 days
  old (`organization_restore_expired` once the window has passed,
  `organization_unavailable` if the row is not archived), and it clears only
  the status and `archived_at`. It never changes `is_default`, re-enables a
  trigger or deployment, or requeues or replays canceled or failed work. After
  a restore, callers submit new requests and an administrator re-enables each
  entry point explicitly. Permanent organization purge is out of B11 scope and
  is registered to E08/I07.

### Recorded deviations and gates

- Dispatch permit. The plan asks for "a serialized permit under these locks so
  archive and membership mutation have a deterministic winner." The
  implementation (`lease_dispatch_permit.py`) works as lock, then decide, then
  record, then release, and only then performs the I/O. The short authority
  transaction rolls back and returns its connection before the provider send,
  tool call, backend call or child launch. No lock is held across that leaf
  call. The winner is decided when the permit takes its locks. A suspension,
  revocation or archive committed before that point is observed, as a denial
  or as the exact-lease archive bypass. One committed after the release is not
  seen by the in-flight leaf call, which runs to its end, and is observed at
  the next dispatch boundary. Writers therefore wait for at most one short
  transaction, never for a provider or tool call.
- Suspension. Suspension changes only `tenants.status` and `updated_at`, and
  records `tenant.suspend`/`tenant.resume`. Memberships, roles, refresh tokens,
  organizations, triggers and deployments are untouched. Queued and admitted
  work is not terminalized: pending inputs stay pending and are not claimed
  while suspended, running work ends at its next permit with
  `tenant_suspended`, and resume makes the inputs claimable again. Agent API
  keys keep the opaque B05 contract and answer `401 AGENT_API_KEY_INVALID`,
  never `tenant_suspended`, so a key does not reveal its company's state. This
  deviates from `org-authz-plan.html:953`, which answers every non-platform
  request of a suspended company with `403 tenant_suspended`.
- Archive of active work (lead decision, pending user confirmation). S05
  (`org-authz-plan.html:1989`) says archive leaves in-progress runs to finish
  and blocks only new ones. The implementation lets only a run that carries the
  materialization pair finish, through the closed lease contract. Every other
  active run fails, including a legacy run that is already `running`; its
  local task is also cancelled. Unprepared execution can still read
  configuration, memory, credentials and history after archive, so the closed
  lease contract cannot bound it. With `prepared_run_lease_enabled` off (the
  default), this applies to every running run.
- `settings.prepared_run_lease_enabled` defaults to `False`. With the flag off,
  runs use the legacy runtime behind the gateway prefix. Turning the flag on
  first needs product decisions on these prerequisites:
  - the OpenAI moderation middleware, which sends outside the lease;
  - fallback model chains;
  - `recursion_limit`;
  - the legacy compatibility policy for `agent_memory_file_write`;
  - the default memory policy `ask` (memory tools).

  Until those are decided, most agents fall back to the legacy runtime.
- Under a lease, trigger-mode output files are deleted with the run root when
  the run is cleaned up. Legacy runs keep them. Artifact persistence for
  trigger lease output is not implemented.
- Selected-MCP discovery runs while the materialization locks are held, so it
  is bounded: 3 s per server and 5 s in total
  (`prepared_mcp_discovery_*_timeout_seconds`). Calls after the marker are
  bounded at 10 s per call and hold no locks.
- Screen items without a 9.7 route have owners in the contract's screen-item
  table: operator company-admin invite/resend/revoke (S03/S04) belongs to the
  new follow-up B12-F1, because B12 only issues the first-admin link at
  creation; `allow_system_models` and system-resource fanout belong to E03,
  the monthly usage and limit column to H03, and secrets-namespace status to
  B16.
- Final-review F4 fixes are on the branch: member and role writes check the
  caller's own membership (enumeration-safe 404) before company status, and
  handing out a pending invitation's live token again records
  `tenant.invitation_reissue`. Two F4 findings are deferred hardening:
  F4-4, Agent API run errors keep raw exception text (pre-existing), and F4-5,
  the `Settings` repr shows `jwt_secret` and `encryption_keys` as plain `str`
  (a `SecretStr` change is still to do).
- Process tooling. `backend/scripts/task10_review_binding.py` (288 pure LOC)
  and the other `backend/scripts/task10_*` scripts are I1 review tooling,
  retired by the B-plan process change and kept only for history. Most of the
  growth in type escapes (`Any` and similar) is in the Task 10 runtime
  modules.
- Gate waivers. `ruff format --check .` reports 74 files, all of them outside
  this branch's diff (pre-existing). `scripts/check_project_facts.py` already
  fails at baseline `6d5323ee` because four tracked
  `docs/exec-plans/active/org-authz-*.md` files are outside `active/` or
  `completed/`. This branch adds a fifth,
  `org-authz-company-administration-contract.md`. Moving the files is deferred
  because sealed `.omo` records cite these paths.
- Not yet executed: the dedicated `company-administration` PostgreSQL lane,
  canonical native `all`, and the migration roundtrip. The development sandbox
  blocks the Docker socket. Real lock contention and deadlock behaviour
  therefore remain unproven, because SQLite ordinary tests cannot show them.
