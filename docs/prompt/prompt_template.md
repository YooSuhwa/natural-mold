<prompt_template>
When writing system prompts, use this structure:

# {Agent Name}

## Role
[1-2 sentence purpose]

## Responsibilities
[Numbered task list]

## Tool Guidelines
### `{tool_name}`
- Purpose: [function]
- When: [trigger condition]
- Caution: [what to avoid]

## Subagent Guidelines
### `{name}`
- Expertise: [domain]
- Delegate when: [condition]

## Workflow
[Step-by-step process]

## Constraints
- ALWAYS: [required behaviors]
- NEVER: [prohibited behaviors]
</prompt_template>
