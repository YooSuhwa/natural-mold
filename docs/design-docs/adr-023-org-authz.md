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
[the supplied plan](../exec-plans/org-authz-plan.html), including its chapter 19
decisions. The original HTML is preserved. Deviations and verification evidence
are recorded in [the execution record](../exec-plans/org-authz-progress.md).

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

- A-13 mentions 19 new tables, while sections 6.1 and 20.1 specify 22 including
  three SSO tables. The canonical schema scope is all 22 tables, not 19.
- Section 18 suggests a separate D-01 PR, conflicting with the detailed two-PR
  delivery in 17.4. Follow 17.4: D-01 remains an independently reviewable commit
  inside PR 1.
- Reserved knowledge/database/workflow types remain model-only until their
  features exist. No placeholder product endpoints are added for them.
