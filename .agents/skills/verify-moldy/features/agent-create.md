# Create an agent

A user creates an agent from `/agents/new`, choosing manual form, template, or the
conversational builder. Saving the manual form persists the agent and opens its settings.

## Sub-features

- `create-chooser` `/agents/new` shows `무엇을 만들고 싶으세요?` with `직접 만들기`, `템플릿으로 만들기`, `대화로 만들기`.
- `create-manual` `/agents/new/manual` form; `에이전트 이름` + `저장` creates the agent.
- `create-template` (not scripted yet) `/agents/new/template`, tab `전체`, pick a template.
- `create-conversational` (not scripted yet) `/agents/new/conversational` builder chat; needs the Builder system LLM, which the scripted stack may not drive end to end.

## How to get to it (user POV)

- Dashboard quick action cards (`대화로 만들기`, `템플릿으로 만들기`).
- `/agents/new` chooser.

## Driving it with verify-moldy

Preconditions:

- Baseline run healthy, logged in.

- **Chooser.** `page.goto('/agents/new')`. Heading `무엇을 만들고 싶으세요?` is visible.
- **Manual form.** Click `직접 만들기` in `main`. URL becomes `/agents/new/manual`.
- **Fill.** Fill placeholder `에이전트 이름` with a unique name.
- **Save.** Click button `저장` (exact). URL becomes `/agents/<id>/settings`.
- **Second view.** `GET /api/agents/<id>` returns the name; after reload the
  `에이전트 이름` field still holds it. The response exposes model metadata under
  `model.id` / `model.display_name`, not a top-level `model_id`.
- **Run it.** `node .agents/skills/verify-moldy/scripts/drive.mjs agent-create-manual`.

## Gotchas

- The normal model catalog is also seeded. Manual creation keeps the form's default
  model (observed: Claude Sonnet 4.6); this journey saves settings without running it.
  Select the scripted model explicitly before adding any chat action to this recipe.
- `실행 동작 고급 설정` is a collapsed `<summary>`; runtime-policy fields are hidden until opened
  (see `frontend/e2e/agent-settings.spec.ts`).
