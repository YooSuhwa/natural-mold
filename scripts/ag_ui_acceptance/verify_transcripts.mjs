/** Replay captured public HTTP frames through the same official SDK as the browser gate. */
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const [helper, ...captures] = process.argv.slice(2)
assert.ok(helper && captures.length, 'pass the SDK verification helper and at least one capture')
const { verifyAgUiTranscript } = await import(pathToFileURL(path.resolve(helper)).href)
const results = []
for (const file of captures) {
  const { name, request, events, expect } = JSON.parse(await readFile(file, 'utf8'))
  const response = await verifyAgUiTranscript({
    sse: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
    threadId: expect.threadId,
    runId: expect.runId,
    outcome: expect.terminal,
    request,
    assistantText: expect.messages?.find((message) => message.role === 'assistant')?.content,
    forbiddenText: expect.forbiddenStrings,
  })
  results.push({ name, frames: events.length, requestChecked: request !== undefined, ...response })
}
console.log(JSON.stringify({ captures: results }, null, 2))
