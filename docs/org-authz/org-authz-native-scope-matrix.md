# B-06 native scope evidence matrix

This matrix describes bounded application evidence on independently migrated m83
PostgreSQL databases owned by the test fixture. Sessions use the database owner
role; these results do not certify RLS, production startup, real provider calls,
browser/manual/human acceptance, shared-resource OpenFGA or full P0–P8 completion.
Every test database is created and dropped by `async_organization_lab`.

`test_private_scope_integration.py` writes two complete ORM fixture graphs for the
same owner in different companies. All twenty tables are tested for exact stored
owner/org/company, disjoint private lists, absent foreign IDs, invalid org/company
pairs and absent ungranted operator rows. Fixture insertion is **not** counted as
production service-writer evidence. Real JWT HTTP checks additionally cover thirteen
detail paths and fourteen list paths; the operator flag is confirmed through
`/api/auth/me` before asserting absent private content.

The same authenticated matrix now executes 28 foreign mutation requests with
valid CSRF and schema-valid payloads, first as the same owner after switching
company, then as an ungranted super-user. Each request must return 404; physical
snapshots of all twenty foreign roots must remain unchanged. This covers Agent,
Skill, MCP, Credential, Tool, Conversation, Share, Artifact, Memory/Proposal,
Builder/SkillBuilder, Blueprint materialization, Trigger, Deployment/API key,
Marketplace item and Installation mutation routes. CredentialDefault has no
production mutation endpoint. Attachment upload/content and binding mutations
remain separate scenarios; this is not every child endpoint's acceptance.

| Scoped model | Native HTTP reads in shared matrix | Actual writer evidence, default and selected second company |
|---|---|---|
| agents | detail, list | `test_private_agent_writers_integration.py`, HTTP create |
| skills | detail, list | `test_private_skill_scope_integration.py`, HTTP create and actual temporary skill files |
| mcp_servers | detail, list | `test_private_credential_scope_integration.py`, HTTP create after company switch |
| credentials | detail, list | `test_private_credential_scope_integration.py`, HTTP create after company switch with dummy cipher data |
| tools | detail, list | `test_private_credential_scope_integration.py`, HTTP create after company switch |
| marketplace_items | detail, list | `test_private_catalog_scope_integration.py`, actual Agent publication |
| conversations | detail, page | `test_private_agent_api_scope_integration.py` and share/artifact cases, actual conversation/thread create |
| conversation_artifacts | detail, library page | `test_private_artifact_scope_integration.py`, real ingest/storage and HTTP content |
| message_attachments | ORM/query in common matrix; owned content in writer scenario | `test_private_attachment_scope_integration.py`, multipart upload and HTTP content |
| memory_records | list | `test_private_memory_scope_integration.py`, HTTP create and proposal approval |
| memory_proposals | detail | `test_private_memory_scope_integration.py`, HTTP create/approve |
| builder_sessions | detail | `test_private_agent_writers_integration.py`, HTTP create |
| skill_builder_sessions | detail, list | `test_private_skill_scope_integration.py`, actual service create |
| agent_blueprints | detail, list | `test_private_install_writers_integration.py`, actual published Agent installation |
| marketplace_installations | common ORM/query matrix; related Skill/Blueprint projections | `test_private_install_writers_integration.py`, actual install HTTP flow |
| agent_triggers | list | `test_private_agent_writers_integration.py`, HTTP create; scheduler sync is stubbed, provider never invoked |
| agent_deployments | list | `test_private_agent_api_scope_integration.py`, HTTP create |
| agent_api_keys | list | `test_private_agent_api_scope_integration.py`, HTTP create and key-authenticated thread creation |
| credential_defaults | ORM/query only; no production read/create endpoint exists | `test_private_catalog_scope_integration.py`, explicit ORM insertion, **not** a service/API writer claim |
| share_links | active-share detail through owned conversation | `test_private_share_scope_integration.py`, HTTP share create |

The writer scenarios select the second company using the real CSRF-protected
`/api/me/active-org` endpoint. Company/member provisioning is fixture setup, not an
organization-management UI claim. All temporary files remain in pytest temporary
storage. The common matrix verifies explicit null/null platform Credential creation
and its absence from private queries while retaining explicit platform candidates;
this does not certify every later platform/shared-resource policy.

The HTTP matrix reproduced a cross-company Agent Blueprint detail leak despite
generic scoped queries being green. Both list/detail routes now apply explicit
scope to Blueprint and joined Installation. The combined query also exposed an
SQLAlchemy auto-correlation failure in installation child checks; explicit
correlation to Installation fixes both native HTTP and existing install regressions.

Inspected results under ignored `output/org-authz/p1/`:

- `b06-exact20-native-http-matrix-r1.log`: red, Blueprint from company A returned 200 in B.
- `b06-exact20-native-http-matrix-r2.log`: red, auto-correlated child predicate lacked FROM.
- `b06-exact20-native-http-matrix-r3.log`: **1 passed**, 5.05s.
- `b06-native-matrix-migration-gates.log`: **8 passed**, 34.49s, including the final authenticated operator assertion and real migration gates.
- `b06-native-blueprint-caller-regression-r2.log`: **91 passed**, 2.78s, isolated mirror removed.
- `b06-native-writer-evidence-batch.log`: **12 passed**, 54.91s, original native writer regression batch.
- `b06-native-second-company-writers.log`: **12 passed**, 47.30s; six scenarios each in default/selected second company.
- `b06-native-second-company-file-writers.log`: **6 passed**, 23.19s; Skill/SkillBuilder, artifact and upload default/second-company cases.
- `b06-native-second-company-credential-tool-mcp.log`: **3 passed**, 12.51s; each case writes in the original and switched company and rejects the original company's ID after switching.
- `b06-native-install-writers.log`: **1 passed**, 5.06s, initial actual Agent publish/install native write case.
- `b06-native-foreign-mutations-r1.log`: **1 passed**, 5.24s; 28 foreign mutations per authenticated actor and unchanged twenty-root snapshots.
- `b06-conversation-mutations-native.log`: **2 passed**, 9.03s; updated matrix and native conversation writers after service mutation hardening.
- `b06-conversation-mutations-regression.log`: **166 passed**, 6.14s; isolated mirror removed. Eighteen direct-service cases cover six conversation mutations in own, foreign and revoked companies; related chat/branch/protocol regressions pass.
- `b06-conversation-projections-regression-r2.log`: **67 passed**, 2.89s; retained-history/foreign-Agent query projections and related regressions, isolated mirror removed.
- `b06-conversation-projections-native.log`: **3 passed**, 12.90s; real PostgreSQL HTTP/root/index recheck after scoped Agent left-joins.
- `b06-conversation-policy-side-native.log`: **5 passed**, 21.07s; side-chat default/selected-company HTTP writers, policy snapshots, Agent API and the expanded 34-path foreign mutation matrix.
- `b06-pinned-summary-native.log`: **2 passed**, 8.98s; actual scoped first-pin concurrency on disposable m83 PostgreSQL, plus the latest 34-path matrix.
- `b06-conversation-deletion-native.log`: **4 passed**, 16.65s; actual m83 own/revoked/foreign-child deletion and unchanged FK references on rejected operations, plus the 34-path matrix. Checkpoint deletion is a scoped stub and offload storage is temporary.

The initial 28-path snapshot above is retained as historical evidence. The current
mutation matrix has 34 paths, adding read-state, branch selection, pinned-summary
write/delete and side-chat create/promote requests. Native pinned concurrency uses
explicit source-message fixtures, not provider-generated or real checkpoint data.

Remaining acceptance work must not be inferred from this table: full-source audit,
foreign writes for each public mutation entrypoint, all parent/child mismatch cases,
latest complete regression/static gates and the independent final review wave.

## Native concurrency added after the first independent review

`test_private_install_concurrency_integration.py` publishes one actual Agent
template and sends four real JWT/CSRF installation requests for the same owner:
two in each company. An actual PostgreSQL holder keeps the production item lock
occupied until all four installers reach the original lock function. After
release, each company has one reused Installation/Blueprint and the two companies
have distinct IDs and exact owner/org/company tuples. No fake lock or injected
winning row is used. Initial native result: **1 passed**, 4.45s.

`test_private_detached_concurrency_integration.py` creates real Agent/Conversation
roots and queues in two companies for one ordinary account. Two actual detached
queue workers restore their own persisted contexts and remain live together at a
bounded provider boundary. While both wait, the real CSRF-protected preference
API switches the user's active company. Each worker then uses its original context
for the actual artifact recorder/storage, memory writer/source binding, persisted
stream events and run completion. Private content reads from the opposite company
return 404; memory and replay payloads contain only their own company marker.
Two real workerless cancellation sweepers subsequently compete on fresh claimed
inputs; each run/input is canceled once without invoking a provider or changing
the earlier file/memory/stream outputs. Initial native result: **1 passed**, 4.86s.

The provider is a deterministic test boundary, not a live LLM or LangGraph graph.
The startup checkpointer is intentionally absent; its optional metrics-baseline
warning does not certify checkpoint persistence, token usage, billing or tracing
provider acceptance. All file roots are pytest-owned temporary directories. The
first attempt corrected an ordinary-account/operator-flag fixture mismatch and
the test follows the production cancellation contract that clears the input's
run pointer. No authorization defect was inferred from those fixture mismatches.

`b06-review-corrections-native.log` records the exact verbose node selections:
**15 passed**, 81.60s, including both new concurrency cases, the 34 foreign-write
HTTP matrix, SkillBuilder original-claim recovery and actual frozen migration
round trips. Temporary owned database inventory is **0** after completion. These
owner-role m83 tests close the concrete concurrency evidence gaps from F3, while
the new exact candidate still requires fresh independent review and full/static
regression. They do not certify RLS, startup, live providers, browser/humans or
the later full P0–P8 phases.
