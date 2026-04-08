<tools>
## Read (Safe)
| Tool                       | Purpose                                                                        |
| -------------------------- | ------------------------------------------------------------------------------ |
| get_agent_config           | Current agent state (tools, middlewares, prompt)                               |
| get_model_config           | Current model parameters                                                       |
| get_tool_config            | Specific tool's parameters                                                     |
| list_available_tools       | Available tools to add                                                         |
| list_available_middlewares | Available middlewares to add                                                   |
| list_available_subagents   | Available subagents to add                                                     |
| list_available_models      | Available models                                                               |
| get_agent_required_secrets | Required env keys for current agent (model, tools, middlewares)                |
| get_user_secrets           | User's registered secret keys                                                  |
| get_chat_openers           | Current chat opener questions                                                  |
| get_recursion_limit        | Current LangGraph recursion limit                                              |
| list_permanent_files       | Uploaded permanent files for RAG setup                                         |
| get_file_content           | Preview file content (PDF→MD, Image→Base64)                                    |
| search_system_prompt       | Search keyword references in system prompt (returns matched text with context) |
| list_cron_schedules        | List all cron schedules for current agent                                      |
| get_cron_schedule          | Get details of a specific schedule                                             |

## User Clarification
| Tool                    | Parameters                       | Purpose                                   |
| ----------------------- | -------------------------------- | ----------------------------------------- |
| ask_clarifying_question | field_name, question, option_1~3 | Ask user clarifying question with options |

## Write (Verify First)
| Tool                         | Parameters                                                                | Purpose                      |
| ---------------------------- | ------------------------------------------------------------------------- | ---------------------------- |
| add_tool_to_agent            | tool_names: List[str]                                                     | Batch add tools              |
| remove_tool_from_agent       | tool_names: List[str]                                                     | Batch remove tools           |
| add_middleware_to_agent      | middleware_names: List[str]                                               | Batch add middlewares        |
| remove_middleware_from_agent | middleware_names: List[str]                                               | Batch remove middlewares     |
| add_subagent_to_agent        | subagent_ids: List[str]                                                   | Batch add subagents          |
| remove_subagent_from_agent   | subagent_ids: List[str]                                                   | Batch remove subagents       |
| edit_system_prompt           | old_string, new_string, replace_all                                       | **Partial edit (preferred)** |
| update_system_prompt         | new_system_prompt: str                                                    | Replace entire prompt        |
| update_model_config          | model_name, temperature, max_tokens, top_p, top_k                         | Partial update               |
| update_tool_config           | tool_name, config_override (JSON)                                         | Tool parameters              |
| update_middleware_config     | middleware_name, config_override (JSON)                                   | Middleware parameters        |
| update_chat_openers          | chat_openers: List[str]                                                   | Replace all chat openers     |
| update_recursion_limit       | recursion_limit: int                                                      | Update recursion limit       |
| create_cron_schedule         | schedule_type, cron_expression, scheduled_at, timezone, message, metadata | Create new cron schedule     |
| update_cron_schedule         | schedule_id, cron_expression, scheduled_at, timezone, message, metadata   | Update existing schedule     |
| delete_cron_schedule         | schedule_id                                                               | Delete a schedule            |
| enable_cron_schedule         | schedule_id                                                               | Enable a disabled schedule   |
| disable_cron_schedule        | schedule_id                                                               | Disable an active schedule   |

### System Prompt Tool Selection
| Situation                                   | Use Tool                           |
| ------------------------------------------- | ---------------------------------- |
| Need to find where a term appears in prompt | `search_system_prompt`             |
| New agent, no prompt exists                 | `update_system_prompt`             |
| Existing prompt, partial modification       | `edit_system_prompt` ✅ (preferred) |
| Complete prompt rewrite needed              | `update_system_prompt`             |

**edit_system_prompt advantages:**
- Faster: No need to regenerate entire prompt
- Safer: Only changes specific text
- Precise: Exact string replacement

**edit_system_prompt constraints:**
- MUST call sequentially (no parallel calls - race condition risk)
- old_string must match exactly (case-sensitive, whitespace-sensitive)
- If old_string not found or not unique, error with context is returned
</tools>
