import fs from 'node:fs/promises'
import path from 'node:path'
import type { APIRequestContext, Page } from '@playwright/test'
import {
  API_BASE,
  apiDeleteOk,
  apiGetJson,
  apiPostJson,
  expect,
  isRecord,
  loginApi,
  test,
} from './fixtures'

const CAPTURE_DIR = path.resolve(
  process.cwd(),
  '..',
  'output',
  'e2e-captures',
  '20260906-chat-modernization',
  'dictation',
)

interface DictationFixture {
  readonly agentId: string
  readonly conversationId: string
}

function idFrom(value: unknown, label: string): string {
  if (!isRecord(value) || typeof value.id !== 'string') {
    throw new Error(`${label} did not return an id`)
  }
  return value.id
}

async function createDictationFixture(request: APIRequestContext): Promise<DictationFixture> {
  const csrfHeaders = await loginApi(request)
  const models = await apiGetJson(request, `${API_BASE}/api/models`)
  if (!Array.isArray(models)) throw new Error('Models response was not an array')
  const scripted = models.find(
    (model) => isRecord(model) && model.provider === 'e2e_scripted' && typeof model.id === 'string',
  )
  if (!scripted) throw new Error('The isolated scripted model was not seeded')

  const agent = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, {
    name: `E2E Dictation ${Date.now()}`,
    system_prompt: 'Exercise browser dictation only. Do not send a response unless asked.',
    model_id: scripted.id,
  })
  const agentId = idFrom(agent, 'Dictation agent')
  const conversation = await apiPostJson(
    request,
    `${API_BASE}/api/agents/${agentId}/conversations`,
    csrfHeaders,
    { title: 'E2E Dictation' },
  )
  return { agentId, conversationId: idFrom(conversation, 'Dictation conversation') }
}

async function installBrowserSpeechDouble(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const recognitions: EventTarget[] = []

    class SpeechRecognitionDouble extends EventTarget {
      continuous = false
      interimResults = false
      lang = ''

      constructor() {
        super()
        recognitions.push(this)
      }

      start(): void {
        this.dispatchEvent(new Event('start'))
      }

      stop(): void {
        this.dispatchEvent(new Event('end'))
      }

      abort(): void {
        const event = new Event('error')
        Object.defineProperties(event, {
          error: { value: 'aborted' },
          message: { value: 'Stopped by the test control' },
        })
        this.dispatchEvent(event)
      }
    }

    Object.defineProperty(window, 'SpeechRecognition', {
      configurable: true,
      value: SpeechRecognitionDouble,
    })
    Object.defineProperty(window, 'webkitSpeechRecognition', {
      configurable: true,
      value: undefined,
    })
    Reflect.set(window, '__moldyDictationDouble', {
      emit(transcript: string, isFinal: boolean) {
        const recognition = recognitions.at(-1)
        if (!recognition) throw new Error('SpeechRecognition was not started')
        const alternative = { confidence: 1, transcript }
        const result = { 0: alternative, isFinal, length: 1 }
        const event = new Event('result')
        Object.defineProperties(event, {
          resultIndex: { value: 0 },
          results: { value: { 0: result, item: () => result, length: 1 } },
        })
        recognition.dispatchEvent(event)
      },
    })
  })
}

async function emitSpeech(page: Page, transcript: string, isFinal: boolean): Promise<void> {
  await page.evaluate(
    ({ nextTranscript, nextIsFinal }) => {
      const controller = Reflect.get(window, '__moldyDictationDouble')
      const emit = controller && Reflect.get(controller, 'emit')
      if (typeof emit !== 'function') {
        throw new Error('Dictation browser double is unavailable')
      }
      Reflect.apply(emit, controller, [nextTranscript, nextIsFinal])
    },
    { nextTranscript: transcript, nextIsFinal: isFinal },
  )
}

async function messageCount(request: APIRequestContext, conversationId: string): Promise<number> {
  const response = await apiGetJson(
    request,
    `${API_BASE}/api/conversations/${conversationId}/messages`,
  )
  if (!isRecord(response) || !Array.isArray(response.messages)) {
    throw new Error('Conversation messages response was invalid')
  }
  return response.messages.length
}

test.describe('Editable browser dictation', () => {
  let fixture: DictationFixture

  test.beforeAll(async ({ request }) => {
    fixture = await createDictationFixture(request)
    await fs.mkdir(CAPTURE_DIR, { recursive: true })
  })

  test.afterAll(async ({ request }) => {
    if (fixture?.agentId) {
      const csrfHeaders = await loginApi(request)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${fixture.agentId}`, csrfHeaders)
    }
  })

  test('keeps partial and final speech editable in the composer without auto-sending', async ({
    page,
    request,
    errors,
  }) => {
    await installBrowserSpeechDouble(page)
    await page.goto(`/agents/${fixture.agentId}/conversations/${fixture.conversationId}`)

    const composer = page.locator('textarea[data-moldy-composer-input="true"]:visible')
    await expect(composer).toHaveCount(1)
    await composer.fill('초안')
    const messagesBefore = await messageCount(request, fixture.conversationId)

    await page.getByRole('button', { name: '음성 입력 시작' }).click()
    await emitSpeech(page, '부분', false)
    await expect(composer).toHaveValue('초안 부분')
    await expect(page.getByText('부분', { exact: true })).toBeVisible()
    await page.screenshot({ path: path.join(CAPTURE_DIR, 'partial-editable.png'), fullPage: true })

    await emitSpeech(page, '확정', true)
    await expect(composer).toHaveValue('초안 확정')
    await expect.poll(() => messageCount(request, fixture.conversationId)).toBe(messagesBefore)
    await page.screenshot({ path: path.join(CAPTURE_DIR, 'final-not-sent.png'), fullPage: true })

    await page.getByRole('button', { name: '음성 입력 중단' }).click()
    await expect(page.getByRole('button', { name: '음성 입력 시작' })).toBeVisible()
    expect(errors.console).toEqual([])
    expect(errors.page).toEqual([])
    expect(errors.network).toEqual([])
  })
})
