# B11/B12 회사 관리 API 계약

이 문서는 B11/B12의 구현 경계와 검토 기준을 고정한다. Pydantic 모델, OpenAPI,
HTTP 동작은 Tasks 6–14가 만드는 실행 가능한 진실이며, 이 문서는 이를 대체하는
parser-tested source of truth가 아니다.

## 공통 계약

- B11은 활성 회사 구성원 범위다. 회사 관리자는 <code>can_manage_tenant</code>,
  감사자는 제한된 집계 읽기만, 일반 구성원은 안전한 개요만 받는다.
  <code>is_super_user</code>만으로 B11 또는 회사 내용 접근 권한이 생기지 않는다.
- B12는 <code>DEPLOYMENT_MODE=multi_tenant</code>의 플랫폼 운영자만 쓴다.
  설치형은 404, SaaS 비운영자는 403이다. 운영자는 메타데이터를 관리하지만
  회사/조직 구성원 자격이나 대화·에이전트·자원 내용 접근을 얻지 않는다.
- B11의 foreign/missing tenant 또는 organization은 외부에 같은 404로 보인다.
- 모든 mutation은 CSRF와 현재 entity ETag의 <code>If-Match</code>를 요구한다.
  Tenant mutation은 Tenant version/updated-at, organization mutation은 Organization
  version/updated-at을 ETag source로 쓰며, 누락·형식 오류·stale 값은
  <code>409 stale_version</code>이다.
- 목록 cursor는 tenant/platform 목록과 정렬에 묶인 opaque cursor다. 기본 limit은
  50, 최대는 200이다. 서명·암호화·일반 Idempotency-Key 규칙은 없다.
- mutation audit은 같은 transaction 안에 <code>tenant_id</code>와 필요한
  <code>org_id</code>를 기록한다. 실패한 mutation은 audit을 남기지 않는다.

## B11 route contract

여기서 schema는 HTTP wire shape다. 내부 모델명과 OpenAPI component 이름은
Tasks 6–10이 이 계약을 구현할 때 정한다.

| Method and path | Authority and request schema | Response/redaction, concurrency | Audit |
| --- | --- | --- | --- |
| <code>GET /api/tenants/{tenant_id}</code> | 활성 tenant member; body 없음 | Tenant safe overview와 <code>ETag: Tenant</code>. Member는 안전한 회사 identity/status만, auditor는 count와 <code>name/member_count</code> organization summary만, admin은 management metadata를 받는다. namespace, key/secret, raw settings, catalog, usage, audit, content ID는 숨긴다. | 없음 |
| <code>PATCH /api/tenants/{tenant_id}</code> | <code>can_manage_tenant</code>; CSRF; Tenant If-Match; <code>TenantOverviewPatch</code>는 B11 allowlist company overview/settings만 포함 | 업데이트된 admin view와 새 Tenant ETag. Reserved/unknown server setting은 보존하며 catalog/policy/limit/usage/audit surface는 없다. | <code>tenant.update</code> |
| <code>GET /api/tenants/{tenant_id}/organizations</code> | 활성 tenant member; <code>cursor</code>, <code>limit</code> | <code>OrganizationPage {items,next_cursor}</code>. Auditor item은 <code>id,name,member_count</code>, admin만 management metadata/administrator state를 받는다. | 없음 |
| <code>POST /api/tenants/{tenant_id}/organizations</code> | <code>can_create_org</code>; CSRF; <code>OrganizationCreate {name,default_member_role: member|builder,administrator?: active_tenant_user_id|email}</code> | tenant-local stable generated slug, requested default role, selected/no-admin state. Name is nonunique, actor receives no implicit org membership. | <code>org.create</code> plus selected-admin membership/role or org-invitation audit |
| <code>PATCH /api/tenants/{tenant_id}/organizations/{org_id}</code> | <code>can_create_org</code>; CSRF; Organization If-Match; <code>OrganizationRename {name}</code> only | Renamed organization, unchanged slug, new Organization ETag. | <code>org.update</code> |
| <code>POST /api/tenants/{tenant_id}/organizations/{org_id}/archive</code> | <code>can_create_org</code>; CSRF; Organization If-Match; <code>OrganizationArchive {confirm_name}</code> | Restore deadline/new Organization ETag. Authorized locked default-org request is <code>409 default_organization_archive_forbidden</code> before stale/name/last-org checks. Nondefault precedence is stale ETag, exact name, then <code>409 last_organization</code>. | <code>org.archive</code> |
| <code>POST /api/tenants/{tenant_id}/organizations/{org_id}/restore</code> | <code>can_create_org</code>; CSRF; Organization If-Match; no successor/default-reassignment input | Restore only inside 30 days. It does not re-enable triggers/deployments or replay work. | <code>org.restore</code> |
| <code>GET /api/tenants/{tenant_id}/members</code> | <code>can_manage_tenant</code>; <code>cursor</code>, <code>limit</code> | <code>TenantMemberPage {items,next_cursor}</code>; identity, active/suspended/removed state, tenant roles, safe org summary only. | 없음 |
| <code>POST /api/tenants/{tenant_id}/members</code> | <code>can_manage_tenant</code>; CSRF; Tenant If-Match; <code>TenantMemberAdd {user_id}</code> for an existing active account | Member has no implied org role; email-first enrollment uses invitations. New Tenant ETag. | <code>tenant.member_add</code> |
| <code>PATCH /api/tenants/{tenant_id}/members/{user_id}</code> | <code>can_manage_tenant</code>; CSRF; Tenant If-Match; <code>TenantMemberStatusPatch {status: active|suspended}</code> | Updated member/new Tenant ETag. Inactive member has no tenant role. | <code>tenant.member_suspend</code> for suspension; active reactivation records its matching membership transition atomically |
| <code>DELETE /api/tenants/{tenant_id}/members/{user_id}</code> | <code>can_manage_tenant</code>; CSRF; Tenant If-Match | Task-5 company-exit effects are atomic for every org; transfer conflict is <code>409 resource_transfer_required</code>. | <code>tenant.member_remove</code> and current per-org exit actions |
| <code>GET /api/tenants/{tenant_id}/roles</code> | <code>can_manage_tenant</code> | <code>TenantRolePage</code> has only tenant <code>admin</code>/<code>auditor</code> role data; roles never unlock org/content. | 없음 |
| <code>PUT /api/tenants/{tenant_id}/roles/{user_id}</code> | <code>can_manage_tenant</code>; CSRF; Tenant If-Match; exact <code>{"roles":["admin"|"auditor",...]}</code>, unique values | Replaces full role set; <code>{"roles":[]}</code> is an active plain member. Final active admin cannot be role-cleared, suspended, or removed. | delta actions <code>tenant.role_grant</code> and <code>tenant.role_revoke</code> |
| <code>GET /api/tenants/{tenant_id}/invitations</code> | <code>can_manage_tenant</code>; <code>cursor</code>, <code>limit</code> | <code>TenantInvitationPage {items,next_cursor}</code> exposes email/role/status/expiry, never plaintext token/hash. | 없음 |
| <code>POST /api/tenants/{tenant_id}/invitations</code> | <code>can_manage_tenant</code>; CSRF; Tenant If-Match; <code>TenantInvitationCreate {email,roles?:[admin|auditor]}</code> | 201 new, 200 retry of same pending email/roles. Plaintext copy-link only in this result; persisted SHA-256 token is seven-day/one-use. | <code>tenant.invitation_create</code> |
| <code>POST /api/tenants/{tenant_id}/invitations/{invitation_id}/resend</code> | <code>can_manage_tenant</code>; CSRF; Tenant If-Match | Replaces usable token; only response has plaintext. | <code>tenant.invitation_resend</code> |
| <code>DELETE /api/tenants/{tenant_id}/invitations/{invitation_id}</code> | <code>can_manage_tenant</code>; CSRF; Tenant If-Match | Revoked invitation/new Tenant ETag; no token/token hash response. | <code>tenant.invitation_revoke</code> |
| <code>POST /api/invitations/accept</code> | authenticated existing account; token body; prefix dispatch | Company token creates/reactivates tenant membership plus optional tenant role, returns <code>org_id:null</code>, and does not change active-org cookie or grant org role/group/capability. | <code>tenant.invitation_accept</code> plus membership delta |
| <code>POST /api/auth/register</code> with company token | token purpose/email/target company <code>invite_only</code>/<code>closed</code> validation before user creation | One transaction creates user, tenant membership/optional role, invite use, and audits. It does not enroll default org or set <code>last_active_org_id</code>/active-org cookie. | existing auth-registration audit plus <code>tenant.invitation_accept</code>/membership delta |

Organization administrator assignment intentionally has no duplicate B11 route. A company
admin uses existing B04 <code>POST /api/orgs/{org_id}/members</code> and
<code>PUT /api/orgs/{org_id}/roles</code> to add an eligible active tenant member before
replacing roles; those routes retain their organization ETag and last-admin guard.

## B12 route contract

| Method and path | Authority and request schema | Response/redaction, concurrency | Audit |
| --- | --- | --- | --- |
| <code>GET /api/admin/tenants</code> | SaaS platform operator; <code>status</code>, <code>search</code>, <code>cursor</code>, <code>limit</code> | <code>PlatformTenantPage {items,next_cursor}</code> has name, slug, status, created time, org/member aggregate, safe limit summary. No content query, raw settings, namespace, key, or secret. | 없음 |
| <code>POST /api/admin/tenants</code> | SaaS platform operator; CSRF; <code>PlatformTenantCreate {name,slug,first_admin_email,first_organization_name,member_limit,monthly_spend_limit,concurrent_run_limit,allow_system_models}</code>. Slug is lowercase ASCII alphanumeric/hyphen, 3–40, no leading/trailing hyphen; email/name valid; limits nonnegative. | 201 has safe tenant/default-org metadata and first-admin plaintext link only. Creates stable <code>default</code> org and no operator membership/role; namespace/token hash are redacted. | <code>tenant.create</code> |
| <code>GET /api/admin/tenants/{tenant_id}</code> | SaaS platform operator | Safe metadata detail: name, slug, status, created time, aggregate org/member count, safe limit summary, B12-editable metadata; Tenant ETag. No content or raw settings/key/namespace/token hash. | 없음 |
| <code>PATCH /api/admin/tenants/{tenant_id}</code> | SaaS platform operator; CSRF; Tenant If-Match; <code>PlatformTenantMetadataPatch</code> is B12 name/current metadata/limit allowlist | Updated safe metadata/new Tenant ETag. | <code>tenant.update</code> |
| <code>POST /api/admin/tenants/{tenant_id}/suspend</code> | SaaS platform operator; CSRF; Tenant If-Match; empty body | Idempotent state/new Tenant ETag; memberships, roles, and refresh tokens remain. New private work sees existing <code>403 tenant_suspended</code>. | <code>tenant.suspend</code> |
| <code>POST /api/admin/tenants/{tenant_id}/resume</code> | SaaS platform operator; CSRF; Tenant If-Match; empty body | Idempotent state/new Tenant ETag. It does not restore archived orgs, re-enable triggers/deployments, or replay work. | <code>tenant.resume</code> |

## Canonical 9.7 crosswalk

This table accounts for exactly the 18 rows at <code>org-authz-plan.html:1210-1227</code>.
A semicolon splits bundled source paths; each path has one owner.

| Row | Exact path grouping | Owner |
| --- | --- | --- |
| 1210 | admin tenant list/create/detail/update/suspend/resume; admin export and schedule-deletion | B12 for list/create/detail/update/suspend/resume; I07 for export/schedule-deletion |
| 1211 | tenant overview/settings | B11 |
| 1212 | tenant organization list/create/rename | B11 |
| 1213 | tenant members/roles/invitations collection/item routes | B11 |
| 1214 | tenant models, credentials, LLM settings, catalog policy | E07 |
| 1215 | tenant usage and audit events; tenant limits | H03 for usage/audit; H06 for limits |
| 1216 | admin authorization-status/failure/reconcile operations | C05 |
| 1217 | tenant organization archive/restore | B11 |
| 1218 | admin exports and cancel-deletion | I07 |
| 1219 | marketplace tenant-publication request/cancel | E09 |
| 1220 | tenant publication-request approve/reject | E09 |
| 1221 | tenant domains/verification | H08 |
| 1222 | tenant SSO connections/test/activate | H08 |
| 1223 | public SSO discover/start/callback | H08 |
| 1224 | public LDAP login | H08 |
| 1225 | tenant publication policy; audit-retention policy; default limits policy | E09; E08; H06 |
| 1226 | organization retention | E08 |
| 1227 | scoped/self usage limits | H06 |

## Exclusions, policy A, and history

- B11/B12 do not implement E07 catalogs, H03 readers, H06 limits, C05 operations,
  E09 publication, E08 retention/purge, H08 SSO/domain, I07 export/deletion, or I08.
- Company invitations never give org membership. Email delivery is deferred; only
  creation/resend results expose a copyable link.
- **Policy A:** every default organization is ineligible for archive. Authorized locked
  requests return <code>409 default_organization_archive_forbidden</code>; B11 never
  reassigns/clears <code>is_default</code>. Restore may preserve a legacy archived default
  without changing that flag.
- The planning baseline <code>6d5323eec9ad27f0e5e6451e0f8fb271fc9dfa55</code>,
  accepted B08 code <code>c5cea625ee1e1470e386525edf120b02178bdbaf</code>, and
  historical migration head <code>b08_agent_lifecycle</code> are immutable inputs,
  not a claim that B08 is final head. Task 16 verifies final head/ancestry.
- After the atomic commit, two independent document reviewers must check the source
  18-row mapping and reject any orphan, duplicate, or deferred route claimed as B11/B12.
  This document does not implement or accept B11/B12 behavior.
