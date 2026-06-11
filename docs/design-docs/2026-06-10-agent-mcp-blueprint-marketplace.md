# Agent Blueprint / MCP Marketplace Plan

> Date: 2026-06-10  
> Status: Draft for implementation planning  
> Source baseline: `main` at `1160da2` after MCP OAuth / Atlassian merge  
> Related docs: `docs/marketplace-resources-prd.md`, `docs/marketplace-resources-spec.md`, `docs/design-docs/adr-017-marketplace-resources.md`, `docs/design-docs/marketplace-module-contracts.md`, `docs/superpowers/plans/2026-06-08-mcp-oauth-atlassian.md`

## 1. Summary

Moldy should extend the existing Skill marketplace into a marketplace for three resource families:

1. **Skill Package** - already implemented.
2. **MCP server** - a shareable/installable MCP server configuration.
3. **Agent Blueprint** - a shareable Agent design installed into a user's Blueprint library, then used to create one or more runnable Agents.

The important naming decision is:

- Use **Agent Blueprint / 에이전트 블루프린트** for Agent sharing.
- Use **MCP / MCP 서버** for MCP sharing.
- Do **not** use "MCP Blueprint" in user-facing copy. It is not a common MCP term and adds an unnecessary product concept.

This keeps the product intuitive:

- Skills install into the Skills menu.
- MCP marketplace items install into the MCP Servers menu.
- Agent marketplace items install into an Agent Blueprint library.
- Runnable Agents are created later from installed Agent Blueprints.

The old `templates` table should not be expanded into the new sharing system. It is a thin prompt/model starter catalog and cannot represent a working Agent with tools, MCP tools, skills, middleware, opener questions, fallback models, and setup requirements. Keep it only as a compatibility bridge while `/agents/new/template` transitions to marketplace-backed Agent Blueprints.

## 2. Current Source Reality

### 2.1 Marketplace foundation

The database and backend already anticipate more than Skill marketplace:

- `backend/app/models/marketplace.py`
  - `MarketplaceItem.resource_type`: `agent`, `mcp`, `skill`
  - `MarketplaceVersion.payload_kind`: `agent_spec`, `mcp_template`, `skill_package`
  - `MarketplaceInstallation.installed_agent_id`, `installed_mcp_server_id`, `installed_skill_id`
  - `MarketplacePublicationLink.source_agent_id`, `source_mcp_server_id`, `source_skill_id`
- `backend/app/marketplace/service.py`
  - Catalog/list/detail projection is mostly resource-type generic.
- `backend/app/marketplace/access.py`
  - Visibility and access checks are generic.
- `backend/app/routers/marketplace.py`
  - Catalog, install/update/uninstall, item metadata, ACL, and moderation routes exist.

The main blocker is `backend/app/marketplace/install_service.py`. `install_item()` currently rejects non-skill resources:

```py
if item.resource_type != "skill":
    logger.info("marketplace_install_unsupported_resource_type %s", item.resource_type)
    raise marketplace_item_not_found()
```

That guard should become a resource-type dispatcher:

```py
if item.resource_type == "skill":
    return await install_skill_item(...)
if item.resource_type == "mcp":
    return await install_mcp_item(...)
if item.resource_type == "agent":
    return await install_agent_blueprint_item(...)
```

The second blocker is `MarketplaceInstallation` target shape. Today `resource_type='agent'` requires `installed_agent_id`, which means "install Agent" creates a runnable Agent. The better UX is "install Agent Blueprint into my Blueprint library" and only later create a runnable Agent. Add `agent_blueprints` and `marketplace_installations.installed_agent_blueprint_id`.

### 2.2 Skill marketplace pattern to reuse

The Skill marketplace implementation is the template for resource marketplace behavior:

- Publish side:
  - `backend/app/marketplace/publish_service.py`
  - snapshots source resource into immutable `MarketplaceVersion`
  - computes stable hash
  - strips and scans secrets
  - creates or updates `MarketplacePublicationLink`
- Install side:
  - `backend/app/marketplace/install_service.py`
  - resolves item/version with access checks
  - creates user-owned installed resource copy
  - creates `MarketplaceInstallation`
  - supports `reuse_or_update`, `new_copy`, `overwrite_existing`
  - marks `needs_setup` when credential bindings are missing
- Projection side:
  - `backend/app/marketplace/origin_service.py`
  - derives publication/installation/credential summaries

MCP and Agent should follow this shape instead of inventing separate sharing systems.

### 2.3 MCP source reality

MCP server model:

- `backend/app/models/mcp_server.py`
  - user-owned `McpServer`
  - `transport`: `stdio`, `sse`, `streamable_http`
  - network transport uses `url`
  - stdio uses `command` and `args`
  - `env_vars` and `headers` can contain credential interpolation templates
  - `credential_id` points to a user credential and must never be copied into marketplace payload
  - health/status fields are runtime state, not marketplace payload
- `backend/app/models/mcp_tool.py`
  - `McpTool` rows are discovered per server
  - `AgentMcpToolLink` links individual MCP tools to Agents
- `backend/app/routers/mcp.py`
  - CRUD, registry creation, probe, import/export, discover/test exist
- `backend/app/mcp/discovery.py`
  - connects and upserts tools
- `backend/app/mcp/auth.py`
  - resolves runtime credential headers
  - refreshes OAuth2 credentials when expired
- `backend/app/mcp/invocation.py`
  - newly added runtime invocation helper

After the latest main pull, MCP OAuth is more mature:

- `backend/alembic/versions/m60_credential_oauth_states.py`
- `backend/app/models/credential_oauth_state.py`
- `backend/app/credentials/mcp_oauth_client.py`
- `backend/app/credentials/definitions/mcp_oauth2.py`
- `backend/tests/test_credentials_oauth_flow.py`
- `backend/tests/test_mcp_oauth_client.py`
- `frontend/e2e/manual-atlassian-oauth.spec.ts`

This matters for marketplace design: marketplace MCP install can now preserve a clean setup flow where a user installs the MCP server item, then authorizes its credential through the credential OAuth flow. Marketplace payloads should carry credential requirements, not credential values or OAuth state.

Security implication: `stdio` MCP servers execute commands from the backend host context. Public marketplace installation of arbitrary stdio commands must be blocked or manual-only unless curated by a super-user.

### 2.4 Agent source reality

Agent model and runtime:

- `backend/app/models/agent.py`
  - fields include `name`, `description`, `system_prompt`, `model_id`, `llm_credential_id`, `model_params`, `middleware_configs`, `opener_questions`, `model_fallback_list`, `identity_mode`, `image_path`, `template_id`
  - relationships include tools, MCP tools, skills, subagents, conversations
- `backend/app/services/agent_service.py`
  - create/update validates ownership of tools, MCP tools, skills, and subagents
  - legacy `template_id` only auto-links recommended tools by name
- `backend/app/services/chat_service.py`
  - builds runtime config from Agent links
  - credentials are resolved at runtime and must not be serialized into Blueprints
- `backend/app/agent_runtime/runtime_component_builder.py`
  - assembles model, tools, MCP tools, skills, middleware, memory tools, filesystem permissions, and subagents

Portability implication: an Agent Blueprint cannot copy raw row IDs for tools, MCP tools, skills, subagents, credentials, conversations, memory, schedules, or API deployments. It must store portable descriptors and resolve them into the installing user's resources.

### 2.5 Legacy templates are insufficient

Current legacy template model:

- `backend/app/models/template.py`
- `backend/app/services/template_service.py`
- `backend/app/routers/templates.py`
- `frontend/src/app/agents/new/template/page.tsx`

Fields are roughly prompt/model/category/recommended tools. This is useful as a starter, but it is not a working Agent package. Keep it during transition, then replace default templates with system Agent Blueprints.

## 3. Product Model

### 3.1 Resource names

| Concept | User-facing name | Internal resource |
| --- | --- | --- |
| Existing Skill marketplace resource | Skill Package / 스킬 패키지 | `resource_type='skill'`, `payload_kind='skill_package'` |
| Reusable MCP server configuration | MCP / MCP 서버 | `resource_type='mcp'`, `payload_kind='mcp_template'` |
| Reusable Agent design | Agent Blueprint / 에이전트 블루프린트 | `resource_type='agent'`, `payload_kind='agent_spec'` |
| User-installed Agent design | Installed Agent Blueprint / 내 블루프린트 | `agent_blueprints` row linked from marketplace installation |
| Legacy prompt starter | Legacy Template / 기존 템플릿 | `templates` table, deprecated |

User-facing MCP copy should say "MCP" or "MCP 서버". The internal `mcp_template` payload kind can remain because it describes the immutable marketplace snapshot, not the product label.

### 3.2 User workflows

#### Publish MCP server

1. User has a working MCP server in `/mcp-servers`.
2. User clicks `MCP 서버 공유`.
3. Publish wizard previews:
   - transport and endpoint/command
   - expected tools from latest discovery
   - credential type required
   - unsafe fields that will be stripped or blocked
4. User selects visibility: private, restricted, public, unlisted.
5. Server creates or updates a marketplace item and immutable `mcp_template` version.

#### Install MCP server

1. User opens a marketplace MCP item.
2. Wizard shows required credential type and expected tools.
3. User optionally binds or creates a credential.
4. Server creates a user-owned `McpServer`.
5. Server creates `MarketplaceInstallation(installed_mcp_server_id=...)`.
6. If credential is complete, server probes/discovers tools.
7. Missing credential or failed auth yields `install_status='needs_setup'`, not a failed install.

#### Publish Agent Blueprint

1. User has a working Agent in `/agents`.
2. User clicks `블루프린트로 공유` from Agent detail/settings.
3. Publish wizard builds an install plan:
   - portable settings copied into Blueprint
   - credentials stripped
   - dependencies classified as portable, publishable, or blocked
4. User confirms dependency behavior.
5. Server creates or updates a marketplace item and immutable `agent_spec` version.

#### Install Agent Blueprint

1. User opens a marketplace Agent Blueprint.
2. Wizard shows:
   - model preference
   - tools/MCP/skills/subagents to install or resolve
   - credentials required
   - unsupported dependencies
3. User chooses a local Blueprint name/category if desired.
4. Server creates a user-owned `agent_blueprints` row containing the versioned `agent_spec` snapshot plus local display metadata.
5. Server creates `MarketplaceInstallation(installed_agent_blueprint_id=...)`.
6. Missing dependency information yields `install_status='needs_setup'`.
7. No runnable Agent is created during marketplace install.
8. User later clicks `이 블루프린트로 에이전트 만들기`; only then does the server resolve/install dependencies and create a runnable `agents` row.

## 4. Core Decisions

### D1. Keep marketplace tables, add `agent_blueprints`

Use existing marketplace tables for catalog, versioning, ACL, install state, publication links, and update availability:

- `MarketplaceItem`
- `MarketplaceVersion`
- `MarketplaceInstallation`
- `MarketplacePublicationLink`
- `MarketplaceItemACL`

Add one installed-resource table:

- `agent_blueprints`

Do not add `mcp_blueprints`. MCP installs already have a natural user-owned runtime resource in `mcp_servers`.

Do not add a new table for Skills. Skills already install into `skills`.

`agent_blueprints` is needed because installing a marketplace Agent Blueprint should feel like installing a Skill or MCP resource into the user's library. Creating a runnable Agent is a later action. If install directly creates an `agents` row, the Agent list becomes cluttered with half-configured runnable resources, and one Blueprint is no longer naturally reusable.

### D2. Agent list shows Agents, Blueprint library shows Blueprints

The Agent dashboard should show runnable `agents` only.

Installed Agent Blueprints should be visible through:

- short term: `/agents/new/template` renamed as `블루프린트에서 시작`
- medium term: `/agents/blueprints` or `/agent-blueprints`

This mirrors existing resource behavior:

- installed Skills appear under Skills and can be attached to Agents
- installed MCP marketplace items appear under MCP Servers and expose MCP tools
- installed Agent Blueprints appear under Blueprint/Templates and can create runnable Agents

### D3. Credential values are never copied

Never copy:

- `Agent.llm_credential_id`
- `Tool.credential_id`
- `McpServer.credential_id`
- decrypted credential data
- OAuth refresh/access tokens
- `credential_oauth_states`
- Skill credential bindings
- raw secret-looking env/header values

Blueprints and marketplace MCP payloads may copy:

- credential definition keys
- requirement keys
- field names
- safe placeholder templates
- labels/descriptions

Installers bind their own credentials.

### D4. Agent Blueprint uses portable dependency references

An Agent Blueprint should not store `tool_id`, `mcp_tool_id`, `skill_id`, or `sub_agent_id` as the install contract.

It should store:

- built-in/registry tool descriptors by `definition_key`
- Skill dependencies by marketplace item/version
- MCP dependencies by marketplace item/version or embedded MCP server descriptor
- subagent dependencies by nested Agent Blueprint reference only after cycle handling exists

Dependency resolution happens in two phases:

1. **Blueprint install** stores the versioned spec in `agent_blueprints`.
2. **Agent creation from Blueprint** resolves or installs dependencies, then creates the runnable `agents` row and links tools/MCP tools/skills/subagents.

### D5. MCP stdio is high-risk

Policy:

- Public/listed MCP marketplace items should default to `sse` or `streamable_http`.
- `stdio` MCP items can only be `private` or `restricted` (`unlisted` is not allowed) with `support_level='manual_only'`.
- Super-user curated system MCP servers may use `stdio` only if command/args are explicitly reviewed.
- Install wizard must show that stdio commands run in the backend environment.

### D6. Local unpublished dependencies are blockers by default

If an Agent uses local user-owned Skill/MCP/subagent resources that are not marketplace-backed, publish should not silently bundle them.

Recommended behavior:

- Show them in the publish wizard as blocked dependencies.
- Offer clear next actions:
  - publish the dependency first
  - remove the dependency
  - for later phase, include as a private dependency through an explicit opt-in

## 5. Data Model

### 5.1 `agent_blueprints`

Proposed table:

```sql
CREATE TABLE agent_blueprints (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  name VARCHAR(200) NOT NULL,
  description TEXT NULL,
  icon_id VARCHAR(80) NULL,
  tags JSON NULL,
  categories JSON NULL,

  spec JSON NOT NULL,
  spec_hash VARCHAR(64) NOT NULL,

  source_marketplace_item_id UUID NULL REFERENCES marketplace_items(id) ON DELETE SET NULL,
  source_marketplace_version_id UUID NULL REFERENCES marketplace_versions(id) ON DELETE SET NULL,
  origin_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
  origin_kind VARCHAR(40) NOT NULL DEFAULT 'imported_by_me',

  install_status VARCHAR(30) NOT NULL DEFAULT 'active',
  is_dirty BOOLEAN NOT NULL DEFAULT FALSE,
  created_agent_count INTEGER NOT NULL DEFAULT 0,

  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
);

CREATE INDEX ix_agent_blueprints_user_updated ON agent_blueprints(user_id, updated_at);
CREATE INDEX ix_agent_blueprints_source_item ON agent_blueprints(source_marketplace_item_id);
```

Add to `marketplace_installations`:

```sql
ALTER TABLE marketplace_installations
  ADD COLUMN installed_agent_blueprint_id UUID NULL
  REFERENCES agent_blueprints(id) ON DELETE CASCADE;
```

Update `ck_marketplace_install_resource_target`:

- `resource_type='skill'` requires `installed_skill_id`
- `resource_type='mcp'` requires `installed_mcp_server_id`
- `resource_type='agent'` requires `installed_agent_blueprint_id` for default Blueprint installs
- keep `installed_agent_id` only for a future direct runnable-Agent install mode if needed

### 5.2 Version payloads

Skill versions use filesystem snapshots. MCP and Agent versions should use canonical JSON payloads:

- `MarketplaceVersion.storage_path = NULL`
- `content_hash = sha256(canonical_json(payload) + requirements + dependency_requirements + execution_profile)`
- `size_bytes = len(canonical_json_bytes)`

Canonical JSON rules:

- sort keys
- compact separators
- explicit `schema_version`
- no user credential IDs
- no runtime row IDs except marketplace dependency references

## 6. MCP Marketplace Design

### 6.1 Payload shape

`MarketplaceVersion.payload_kind = 'mcp_template'`

```json
{
  "schema_version": 1,
  "resource": "mcp_server",
  "name": "Atlassian MCP",
  "description": "Jira and Confluence tools through Atlassian MCP",
  "transport": "streamable_http",
  "url": "https://mcp.atlassian.com/v1/mcp",
  "command": null,
  "args": [],
  "env_vars": {},
  "headers": {
    "Authorization": "=Bearer {{ $credentials.access_token }}"
  },
  "credential_definition_key": "mcp_oauth2",
  "registry_key": "atlassian",
  "tool_snapshot": [
    {
      "name": "search_jira",
      "description": "Search Jira issues",
      "input_schema": {}
    }
  ],
  "install_defaults": {
    "discover_on_install": true,
    "enabled_tool_names": ["search_jira"]
  },
  "security": {
    "requires_network": true,
    "stdio_risk": false,
    "support_level": "one_click"
  }
}
```

Notes:

- `credential_id` is not included.
- `tool_snapshot` is documentation and install planning only.
- Actual `mcp_tools` must come from discovery when possible.
- If discovery fails, show expected tools from snapshot but do not link phantom MCP tools at runtime.

### 6.2 Credential requirements

Generate `MarketplaceVersion.credential_requirements` from:

- source `McpServer.credential_id` definition key if present
- registry `credential_definition_key`
- header/env placeholders that require credential fields

Example:

```json
[
  {
    "key": "mcp_auth",
    "definition_key": "mcp_oauth2",
    "required": true,
    "label": "MCP OAuth credential",
    "description": "Used to authenticate the MCP server",
    "fields": ["access_token", "refresh_token"],
    "injection": "config",
    "scope": "user"
  }
]
```

### 6.3 Publish validation

Before creating a version:

1. Verify source server ownership.
2. Build sanitized payload.
3. Strip or reject unsafe fields:
   - `credential_id`
   - resolved auth headers
   - raw access tokens
   - refresh tokens
   - `.env`-like values inside env/header payloads
4. Run recursive secret scan over canonical JSON.
5. Apply stdio policy.
6. Compute content hash.
7. Upsert `MarketplacePublicationLink(source_mcp_server_id=...)`.

### 6.4 Install behavior

Install creates a normal `McpServer`:

- `user_id = current_user.id`
- `name = name_override or item.name`
- `transport/url/command/args/env_vars/headers` from payload
- `credential_id = selected binding or NULL`
- `status = 'unknown'`
- `is_system = false`

Then:

- create `MarketplaceInstallation(installed_mcp_server_id=server.id)`
- if required credential is missing, set `install_status='needs_setup'`
- if credential is supplied, run discovery
- if discovery auth fails, keep server and set auth/setup state
- if discovery succeeds, install active and expose discovered tools

### 6.5 Update behavior

Use existing update strategies:

- `keep_current`: update installation pointer only
- `install_new_copy`: create a new `McpServer`
- `overwrite`: update existing `McpServer` config, clear runtime MCP cache, rediscover tools

Overwrite must preserve:

- `McpServer.id`
- existing `AgentMcpToolLink` rows when matching tool names still exist

If rediscovery changes tool IDs, remap links by `(server_id, tool_name)`.

## 7. Agent Blueprint Design

### 7.1 Payload shape

`MarketplaceVersion.payload_kind = 'agent_spec'`

```json
{
  "schema_version": 1,
  "resource": "agent_blueprint",
  "agent": {
    "name": "Daily Research Assistant",
    "description": "Searches the web, summarizes findings, and writes reports.",
    "system_prompt": "...",
    "identity_mode": "per_user",
    "model": {
      "provider": "openai",
      "model_name": "gpt-5-mini",
      "base_url": null,
      "preferred_model_id": "same-db-fast-path-uuid"
    },
    "model_params": {
      "temperature": 0.2,
      "recursion_limit": 50
    },
    "model_fallback_chain": [
      {
        "provider": "anthropic",
        "model_name": "claude-sonnet-4-5",
        "base_url": null
      }
    ],
    "middleware_configs": [],
    "opener_questions": ["오늘 조사할 주제는 무엇인가요?"]
  },
  "capabilities": {
    "tools": [
      {
        "kind": "registry_tool",
        "definition_key": "builtin:web_search",
        "name": "Web Search",
        "parameters": {},
        "requires_credential": false
      }
    ],
    "skills": [],
    "mcp_tools": [],
    "subagents": []
  },
  "setup": {
    "required_credentials": [],
    "warnings": [],
    "blocked_dependencies": []
  }
}
```

### 7.2 Publish rules

Copy:

- `name`, `description`, `system_prompt`
- model preference descriptors
- safe `model_params`
- `middleware_configs` after registry validation
- `opener_questions`
- safe tool parameters
- dependency references

Do not copy:

- credentials
- conversations
- memory records/proposals/settings
- schedules/triggers
- Agent API deployments/keys
- generated artifacts
- message events
- runtime checkpoint state

### 7.3 Install behavior

Marketplace install of an Agent resource:

1. resolves item/version/access
2. validates payload kind `agent_spec`
3. creates or updates `agent_blueprints`
4. creates `MarketplaceInstallation(installed_agent_blueprint_id=...)`
5. computes `install_status`
6. does not create `agents`

Create Agent from Blueprint:

1. loads installed `agent_blueprints` for current user
2. resolves target model
3. resolves model credential binding if supplied
4. resolves builtin/registry tools
5. installs or reuses Skill dependencies
6. installs or reuses MCP server dependencies
7. links MCP tools by discovered tool name
8. creates `Agent`
9. links tools, skills, MCP tools, subagents
10. increments `agent_blueprints.created_agent_count`

### 7.4 Install status

Use:

- `active`: Blueprint spec is installed and no required setup is missing
- `needs_setup`: required credentials or dependencies are missing
- `disabled`: marketplace item or local installation disabled
- `uninstalled`: installation link removed

The UI should guide setup before materialization, but create-agent may still allow a partially configured Agent if the user explicitly accepts warnings.

## 8. API Design

### 8.1 Schemas

Add to `backend/app/marketplace/schemas.py` or split into resource-specific modules if the file grows too large.

```py
class PublishMcpServerIn(BaseModel):
    item_id: uuid.UUID | None = None
    visibility: Literal["private", "restricted", "public", "unlisted"]
    name: str
    description: str | None = None
    icon_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    release_notes: str | None = None
    acl_user_ids: list[uuid.UUID] = Field(default_factory=list)
    include_tool_snapshot: bool = True

class PublishAgentBlueprintIn(BaseModel):
    item_id: uuid.UUID | None = None
    visibility: Literal["private", "restricted", "public", "unlisted"]
    name: str
    description: str | None = None
    icon_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    release_notes: str | None = None
    acl_user_ids: list[uuid.UUID] = Field(default_factory=list)
    dependency_strategy: Literal["block_non_portable", "publish_private_dependencies"] = "block_non_portable"
    include_subagents: Literal["none", "published_only", "nested"] = "none"

class MarketplaceInstallPlanOut(BaseModel):
    item_id: uuid.UUID
    version_id: uuid.UUID
    resource_type: Literal["agent", "mcp", "skill"]
    status: Literal["ready", "needs_credentials", "blocked"]
    required_credentials: list[CredentialRequirementOut]
    dependencies: list[dict[str, Any]]
    warnings: list[str]
    blockers: list[str]

class AgentBlueprintOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    source_marketplace_item_id: uuid.UUID | None = None
    source_marketplace_version_id: uuid.UUID | None = None
    installation_id: uuid.UUID | None = None
    install_status: Literal["active", "needs_setup", "disabled", "uninstalled"]
    is_dirty: bool = False
    created_agent_count: int = 0
    created_at: datetime
    updated_at: datetime

class CreateAgentFromBlueprintIn(BaseModel):
    name: str | None = None
    model_id: uuid.UUID | None = None
    model_fallback_ids: list[uuid.UUID] | None = None
    credential_bindings: dict[str, uuid.UUID] = Field(default_factory=dict)
    dependency_strategy: Literal["reuse_existing", "install_missing", "always_new"] = "install_missing"
    dependency_bindings: dict[str, dict[str, uuid.UUID]] = Field(default_factory=dict)
```

### 8.2 Routes

```http
POST /api/marketplace/items/from-mcp/{server_id}
POST /api/marketplace/items/{item_id}/versions/from-mcp/{server_id}

POST /api/marketplace/items/from-agent/{agent_id}
POST /api/marketplace/items/{item_id}/versions/from-agent/{agent_id}

GET  /api/marketplace/items/{item_id}/install-plan?version_id=<optional>
POST /api/marketplace/items/{item_id}/install

GET    /api/agent-blueprints
GET    /api/agent-blueprints/{blueprint_id}
POST   /api/agent-blueprints/{blueprint_id}/create-agent
PATCH  /api/agent-blueprints/{blueprint_id}
DELETE /api/agent-blueprints/{blueprint_id}
```

Credential binding keys:

- MCP direct credential: `mcp_auth`
- Agent model credential: `agent.llm`
- Tool credential: `tool.<definition_key>`
- Skill credential: `skill.<dependency_key>.<requirement_key>`
- MCP dependency credential: `mcp.<dependency_key>.<requirement_key>`

## 9. Backend Implementation Slices

### Slice A: shared marketplace payload helpers

Files:

- `backend/app/marketplace/payloads.py`
- `backend/app/marketplace/secret_scan.py`
- `backend/app/marketplace/schemas.py`

Tasks:

1. Add canonical JSON hash helper.
2. Add recursive secret scanner for dict/list/string payloads.
3. Add resource-specific payload validators.
4. Add install plan shape shared by MCP and Agent.
5. Add deterministic hash and secret rejection tests.

### Slice B: MCP server publish/install

Files:

- `backend/app/marketplace/mcp_server.py`
- `backend/app/routers/marketplace.py`
- `backend/app/marketplace/install_service.py`
- `backend/app/marketplace/publish_service.py` or dispatcher refactor
- `backend/app/marketplace/origin_service.py`

Tasks:

1. Build sanitized MCP payload from `McpServer`.
2. Generate credential requirements.
3. Publish new marketplace item/version.
4. Create/update `MarketplacePublicationLink(source_mcp_server_id=...)`.
5. Install marketplace MCP item into `McpServer`.
6. Run discovery when possible.
7. Create `MarketplaceInstallation(installed_mcp_server_id=...)`.
8. Extend installation summary for MCP setup state.
9. Mark MCP installation dirty from MCP update/delete paths.

### Slice C: Agent Blueprint publish

Files:

- `backend/app/marketplace/agent_blueprint.py`
- `backend/app/services/agent_service.py`
- `backend/app/services/chat_service.py` only if helper extraction is useful

Tasks:

1. Load Agent with model, tools, MCP tools, skills, subagents.
2. Build portable `agent_spec`.
3. Convert model/fallback UUIDs to provider/model descriptors.
4. Convert system/registry tools to descriptors.
5. Convert marketplace-backed skills to dependency references.
6. Convert MCP tool links to MCP dependency references by server/item and tool name.
7. Detect non-portable dependencies and return blockers.
8. Validate middleware configs against registry.
9. Create `MarketplaceVersion(payload_kind='agent_spec')`.
10. Create/update `MarketplacePublicationLink(source_agent_id=...)`.

### Slice D: Agent Blueprint install and materialization

Files:

- `backend/app/models/agent_blueprint.py`
- `backend/app/routers/agent_blueprints.py`
- `backend/app/marketplace/agent_blueprint.py`
- `backend/app/marketplace/install_service.py`
- Alembic migration after M60

Tasks:

1. Add `agent_blueprints` model/table.
2. Add `installed_agent_blueprint_id` to `MarketplaceInstallation`.
3. Install Agent marketplace item into `agent_blueprints`.
4. Create `MarketplaceInstallation(installed_agent_blueprint_id=...)`.
5. Keep installed Blueprint reusable.
6. Add create-agent-from-blueprint service.
7. Resolve tools, skills, MCP servers, MCP tools during materialization.
8. Create runnable `Agent`.
9. Increment `created_agent_count`.

### Slice E: frontend marketplace enablement

Files:

- `frontend/src/lib/types/marketplace.ts`
- `frontend/src/lib/api/marketplace.ts`
- `frontend/src/lib/hooks/use-marketplace.ts`
- `frontend/src/app/marketplace/page.tsx`
- `frontend/src/components/marketplace/*`
- `frontend/src/app/agents/[agentId]/settings/page.tsx`
- `frontend/src/app/mcp-servers/page.tsx`
- `frontend/src/app/agents/new/template/page.tsx`
- `frontend/messages/ko.json`
- `frontend/messages/en.json`

Tasks:

1. Enable Agent Blueprint and MCP marketplace tabs.
2. Route open CTAs by resource type:
   - skill: `/skills?detailId=<id>`
   - agent: `/agents/blueprints/<id>` or `/agents/new/template?blueprintId=<id>` during transition
   - mcp: `/mcp-servers?detailId=<id>`
3. Split install wizard by `resource_type`.
4. Add Agent Blueprint publish wizard entry point.
5. Add MCP server publish wizard entry point.
6. Add install plan UI.
7. Add installed Agent Blueprint library surface.
8. Add `이 블루프린트로 에이전트 만들기` flow.
9. Rename template creation surface to `블루프린트에서 시작`.
10. Add i18n messages and run `pnpm lint:i18n`.
11. Run `pnpm lint:design-system` after UI changes.

### Slice F: legacy template migration

Tasks:

1. Seed system Agent Blueprints from `backend/app/seed/default_templates.py`.
2. Keep `/api/templates` read-only for compatibility.
3. Update `/agents/new/template` to query installed/system Agent Blueprints first.
4. Use legacy templates only as fallback.
5. After the Blueprint route stabilizes, stop writing `Agent.template_id`.

## 10. Frontend UX

### 10.1 Marketplace catalog

Tabs:

- All
- Skills
- Agent Blueprints
- MCP
- Installed

Cards should show:

- resource badge
- publication/installation badges
- credential setup state
- support level
- version

### 10.2 Agent publish

Entry point:

- Agent detail page
- Agent settings page

Button:

- Korean: `블루프린트로 공유`
- English: `Share as blueprint`

Wizard:

1. Review
2. Dependencies
3. Metadata
4. Visibility
5. Confirm
6. Done

### 10.3 MCP publish

Entry point:

- MCP server detail dialog/page

Button:

- Korean: `MCP 서버 공유`
- English: `Share MCP server`

Wizard:

1. Review connection
2. Tools snapshot
3. Credential requirement
4. Visibility
5. Confirm
6. Done

### 10.4 Agent install

Wizard:

1. Review Blueprint
2. Install plan
3. Credentials
4. Local name/category
5. Confirm
6. Done

Done CTA:

- Open installed Blueprint
- Create Agent from Blueprint

### 10.5 MCP install

Wizard:

1. Review MCP server
2. Credential
3. Test/discover
4. Confirm
5. Done

Done CTA:

- Open installed MCP server

## 11. Testing Plan

### 11.1 Backend tests

Add:

- `backend/tests/test_marketplace_payloads.py`
  - canonical JSON hash stable
  - secret scanner rejects token-looking fields
- `backend/tests/test_marketplace_mcp_server.py`
  - publish MCP strips credential IDs
  - publish rejects secret-looking headers/env
  - public stdio policy blocks unsafe publish
  - install creates `McpServer`
  - missing credential yields `needs_setup`
  - discovery success creates `McpTool`
  - overwrite preserves server ID
- `backend/tests/test_marketplace_agent_blueprint.py`
  - publish Agent strips credentials/conversations/memory/artifacts
  - model fallback descriptors are portable
  - local unpublished skill blocks publish
  - marketplace skill dependency installs and links
  - MCP dependency installs and links by tool name
  - marketplace install creates `agent_blueprints` and `MarketplaceInstallation`
  - create-agent materializes runnable `Agent`
  - create-agent can be repeated from one Blueprint
  - overwrite/update preserves installed Blueprint ID and local created Agents

Run:

```bash
cd backend
uv run pytest tests/test_marketplace_mcp_server.py tests/test_marketplace_agent_blueprint.py
uv run pytest tests/test_marketplace_install.py tests/test_marketplace_publish.py tests/test_marketplace_access.py
uv run ruff check app tests
```

### 11.2 Frontend tests

Add or extend:

- `frontend/tests/pages/marketplace.test.tsx`
- `frontend/tests/pages/marketplace-detail.test.tsx`
- `frontend/tests/pages/agents-new-template.test.tsx`
- `frontend/tests/components/marketplace/*`
- `frontend/tests/unit/api/marketplace.test.ts`
- `frontend/tests/unit/hooks/use-marketplace.test.tsx`

Run:

```bash
cd frontend
pnpm test
pnpm lint
pnpm lint:i18n
pnpm lint:design-system
pnpm build
```

### 11.3 E2E

Add Playwright coverage:

- Install marketplace MCP server with missing credential shows setup state.
- Install marketplace MCP server with dummy/mock MCP succeeds and discovers tools.
- Publish Agent Blueprint from an existing Agent with only system tools succeeds.
- Publish Agent Blueprint with local unpublished skill shows blocker.
- Install Agent Blueprint saves it into the user's Blueprint library.
- Create Agent from installed Blueprint creates a runnable Agent.
- Marketplace Agent/MCP tabs show cards and open installed resources.

Capture artifacts under:

```text
output/e2e-captures/<YYYYMMDD>-agent-mcp-marketplace/
```

## 12. Implementation Order

Recommended order:

1. **Shared payload helpers**
   - canonical JSON hash
   - recursive secret scan
   - install plan schema
2. **MCP server backend**
   - publish, version, install, update
   - tests
3. **MCP frontend**
   - enable MCP tab
   - publish/install wizard
   - tests
4. **Agent Blueprint backend MVP**
   - publish/install into `agent_blueprints`
   - materialize installed Blueprint into runnable Agent
   - support system tools and marketplace Skill dependencies
   - block non-portable MCP/subagent dependencies
5. **Agent Blueprint MCP dependency support**
   - install/reuse marketplace MCP servers
   - link MCP tools by name
6. **Agent Blueprint frontend**
   - enable Agent tab
   - publish/install wizard
   - installed Blueprint library
   - create-agent-from-blueprint flow
   - `/agents/new/template` transition
7. **Legacy template migration**
   - seed system Agent Blueprints
   - fallback compatibility
8. **Hardening**
   - E2E
   - audit event coverage
   - update/dirty behavior
   - docs sync

This order avoids a trap: Agent sharing is mostly dependency resolution. MCP marketplace install is the missing dependency primitive, so it should come first.

## 13. Migration and Compatibility

### 13.1 Required migration after M60

Add one focused migration after `m60_credential_oauth_states.py`:

- create `agent_blueprints`
- add `marketplace_installations.installed_agent_blueprint_id`
- update marketplace installation target check constraint
- add ORM relationship from `MarketplaceInstallation` to `AgentBlueprint`
- add `AgentBlueprint` to `backend/app/models/__init__.py`

No new core marketplace item/version tables are needed.

### 13.2 Existing Skill behavior

Skill marketplace behavior must remain compatible:

- existing install endpoint body still works
- current Skill publish wizard remains valid
- current Skill E2E remains valid
- `resource_type='skill'` behavior does not regress

### 13.3 Legacy templates

Do not delete in first implementation.

Keep:

- `backend/app/models/template.py`
- `backend/app/routers/templates.py`
- `frontend/src/app/agents/new/template/page.tsx`

Change only after system Agent Blueprints exist.

## 14. Acceptance Criteria

### MCP

- A user can publish a working MCP server as a private/restricted/public/unlisted marketplace item.
- Credential IDs and secret values are never present in version payload.
- OAuth state and tokens are never present in version payload.
- Public arbitrary stdio publish is blocked or marked manual-only.
- Another user can install the MCP server from marketplace.
- Missing credential creates `needs_setup`, not a 500.
- Valid credential runs discovery and creates MCP tools.
- Installed MCP server can be attached to an Agent through existing tools picker.

### Agent Blueprint

- A user can publish an existing Agent as an Agent Blueprint.
- Payload excludes credentials, conversations, memory, artifacts, schedules, and API deployments.
- Installing the Blueprint creates a user-owned `agent_blueprints` row, not a runnable Agent.
- A separate create-agent action can create one or more runnable Agents from an installed Blueprint.
- System tools and safe registry tools resolve correctly.
- Marketplace Skill dependencies install and link correctly.
- MCP dependencies install/reuse marketplace MCP servers and link MCP tools by name.
- Non-portable local dependencies are shown before publish and are not silently leaked.
- Update available, dirty, install new copy, keep current, and overwrite work for installed Agent Blueprints.

### Legacy template transition

- `/agents/new/template` still works during migration.
- System Agent Blueprints can replace default templates.
- No user-facing duplicate confusion between "template" and "blueprint" remains after transition.

## 15. Risks and Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Agent publish leaks credentials | Critical | Strip IDs, recursive secret scan, token fixtures in tests |
| MCP publish leaks OAuth tokens/state | Critical | Explicit denylist for credential/OAuth fields, tests against M60 OAuth flow |
| MCP stdio becomes remote command execution path | Critical | Block public stdio, manual-only private stdio, super-user review for curated items |
| Agent materialization creates broken resources | High | Install plan, `needs_setup`, dependency blockers before create-agent confirmation |
| Nested subagents create cycles | High | MVP block nested subagents, future max depth + cycle detection |
| Existing Skill marketplace regresses | High | Keep Skill service behavior, add regression tests |
| Legacy templates conflict with Blueprints | Medium | Marketplace-backed route first, legacy fallback, later deprecation |
| Discovering MCP tools during install is slow/flaky | Medium | Preserve installed server, mark setup/auth state, allow retry from MCP page |

## 16. Final Recommendation

Build **MCP marketplace install/share first**, then **Agent Blueprint**.

Do not invest further in the legacy `templates` model except as a compatibility bridge. The current marketplace schema already anticipated Agent/MCP resources, and the Skill marketplace code provides the implementation pattern.

The product should feel like:

- "I made a working MCP server. I can share it in the marketplace."
- "I made a working Agent. I can save/share its Blueprint."
- "Someone else can install that Blueprint into their library, then create one or more Agent copies from it."
- "Secrets are never shared. Setup requirements are explicit."
- "MCP servers are reusable marketplace resources that Agent Blueprints can depend on."

This gives Moldy one coherent marketplace model instead of separate Skill, MCP, and Agent sharing systems.
