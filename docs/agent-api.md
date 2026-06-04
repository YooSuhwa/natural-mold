# Agent API

Moldy Agent API lets external server-side systems call deployed Moldy agents with
API keys. API keys must not be embedded in browser code.

## Concepts

- **Deployment**: an agent published for external API calls. Runtime requests use
  the deployment `public_id` as `agent_id`.
- **API key**: a user-owned secret with scopes such as `invoke`, `stream`, and
  `read`. A key can allow all deployments or a selected set of deployments.
- **Thread**: durable external session mapped to an internal API conversation.
- **Run**: one invocation against a deployment, either blocking or streaming.
- **Limits**: per-deployment rate and daily token quota controls are planned and
  shown as preparation status in the UI. They are not enforced by the v1 runtime yet.

## Authentication

```http
Authorization: Bearer moldy_sk_<key_id>_<secret>
```

The API also accepts:

```http
X-Api-Key: moldy_sk_<key_id>_<secret>
X-Auth-Scheme: moldy-api-key
```

## Health and Discovery

```bash
curl http://localhost:8001/v1/health
```

```bash
curl http://localhost:8001/v1/agents \
  -H "Authorization: Bearer $MOLDY_API_KEY"
```

## Blocking Run

```bash
curl -X POST http://localhost:8001/v1/runs/wait \
  -H "Authorization: Bearer $MOLDY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "agent_12345678",
    "input": {
      "messages": [
        { "role": "user", "content": "요약해줘" }
      ]
    },
    "user": "external-user-123"
  }'
```

## Streaming Run

```bash
curl -N -X POST http://localhost:8001/v1/runs/stream \
  -H "Authorization: Bearer $MOLDY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "agent_12345678",
    "input": {
      "messages": [
        { "role": "user", "content": "스트리밍으로 답해줘" }
      ]
    }
  }'
```

External SSE events are stable: `run_start`, `message`, `tool_update`,
`interrupt_blocked`, `run_end`, and `error`.

## Stateful Thread

```bash
curl -X POST http://localhost:8001/v1/threads \
  -H "Authorization: Bearer $MOLDY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{ "agent_id": "agent_12345678", "user": "external-user-123" }'
```

Use the returned `thr_...` id:

```bash
curl -X POST http://localhost:8001/v1/threads/thr_xxx/runs/wait \
  -H "Authorization: Bearer $MOLDY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "agent_12345678",
    "input": {
      "messages": [
        { "role": "user", "content": "방금 질문을 기억해?" }
      ]
    }
  }'
```

## Compatibility Endpoints

Dify-style chat:

```bash
curl -X POST http://localhost:8001/v1/agents/agent_12345678/chat-messages \
  -H "Authorization: Bearer $MOLDY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{ "query": "요약해줘", "response_mode": "blocking", "user": "abc-123" }'
```

Dify-style workflow:

```bash
curl -X POST http://localhost:8001/v1/workflows/run \
  -H "Authorization: Bearer $MOLDY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "inputs": { "agent_id": "agent_12345678", "query": "Run workflow" },
    "response_mode": "blocking",
    "user": "abc-123"
  }'
```

OpenAI-compatible chat completion:

```bash
curl -X POST http://localhost:8001/v1/chat/completions \
  -H "Authorization: Bearer $MOLDY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agent_12345678",
    "messages": [
      { "role": "user", "content": "Hello" }
    ]
  }'
```

Set `"stream": true` to receive OpenAI-style `chat.completion.chunk` SSE
payloads followed by `data: [DONE]`.
