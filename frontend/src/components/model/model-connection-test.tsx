'use client'

/**
 * Model Connection Test card.
 *
 * Single component reused in five touchpoints (DataTable row action, bulk
 * "Test Selected", Add dialog Custom-ID tab, Edit dialog, ModelSelect Custom
 * mode). Mirrors the prior-art UX from upstream gateway dashboards (success
 * card, error card, "Show Details" tabs, copy-curl) — see NOTICES.md for
 * attribution.
 */

import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Copy,
  Loader2,
  RotateCw,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Separator } from '@/components/ui/separator'
import { useTestModel } from '@/lib/hooks/use-models'
import type { ModelTestErrorKind, ModelTestResponse } from '@/lib/types/model'

const ERROR_KIND_LABEL: Record<ModelTestErrorKind, string> = {
  auth: '인증 실패',
  not_found: '모델 없음',
  rate_limit: '호출 한도',
  timeout: '시간 초과',
  other: '오류',
}

interface BaseProps {
  credentialId: string
  autoStart?: boolean
  onComplete?: (result: ModelTestResponse) => void
  /** Optional human label used in the loading message. */
  modelLabel?: string
  /** Render the cost-warning banner. Defaults to true. */
  showCostBanner?: boolean
  className?: string
}

interface RegisteredProps extends BaseProps {
  mode: 'registered'
  modelId: string
}

interface PreviewProps extends BaseProps {
  mode: 'preview'
  provider: string
  modelName: string
  baseUrl?: string | null
}

type Props = RegisteredProps | PreviewProps

export function ModelConnectionTest(props: Props) {
  const {
    credentialId,
    autoStart = true,
    onComplete,
    modelLabel,
    showCostBanner = true,
    className,
  } = props

  const test = useTestModel()
  const [result, setResult] = useState<ModelTestResponse | null>(null)
  const [showDetails, setShowDetails] = useState(false)

  const runTest = useCallback(async () => {
    setShowDetails(false)
    setResult(null)
    try {
      const data =
        props.mode === 'registered'
          ? await test.mutateAsync({
              mode: 'registered',
              modelId: props.modelId,
              credentialId,
            })
          : await test.mutateAsync({
              mode: 'preview',
              payload: {
                provider: props.provider,
                model_name: props.modelName,
                base_url: props.baseUrl ?? null,
                credential_id: credentialId,
              },
            })
      setResult(data)
      onComplete?.(data)
    } catch (e) {
      // Mutation errors (network/HTTP) → render synthetic error card.
      const synthetic: ModelTestResponse = {
        success: false,
        response: null,
        latency_ms: 0,
        tokens_in: null,
        tokens_out: null,
        estimated_cost_usd: null,
        error: {
          kind: 'other',
          message: e instanceof Error ? e.message : String(e),
          raw: null,
        },
        raw_request: null,
        raw_response: null,
        curl_command: null,
      }
      setResult(synthetic)
      onComplete?.(synthetic)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [credentialId, JSON.stringify(props), onComplete])

  useEffect(() => {
    if (!autoStart) return
    if (!credentialId) return
    runTest()
    // We intentionally fire on mount only; subsequent runs are user-initiated.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const isLoading = test.isPending && !result
  const status: 'idle' | 'loading' | 'success' | 'error' = isLoading
    ? 'loading'
    : !result
      ? 'idle'
      : result.success
        ? 'success'
        : 'error'

  return (
    <div
      className={
        className ??
        'space-y-3 rounded-lg border bg-background p-4 text-sm shadow-sm'
      }
      data-testid="model-connection-test"
    >
      {showCostBanner && (
        <p className="rounded border border-amber-300 bg-amber-50 p-2 text-[11px] text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-200">
          이 테스트는 실제 API를 호출합니다 (예상 ~$0.0001).
        </p>
      )}

      {status === 'idle' && (
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">
            준비됨 — Test 버튼을 눌러 시작하세요.
          </span>
          <Button
            size="sm"
            onClick={runTest}
            disabled={!credentialId || test.isPending}
          >
            Test now
          </Button>
        </div>
      )}

      {status === 'loading' && (
        <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            Testing connection to{' '}
            <span className="font-medium text-foreground">
              {modelLabel ?? 'this model'}
            </span>
            ...
          </p>
        </div>
      )}

      {status === 'success' && result && (
        <SuccessCard result={result} modelLabel={modelLabel} />
      )}

      {status === 'error' && result && (
        <ErrorCard result={result} onRetry={runTest} />
      )}

      {result && (result.curl_command || result.raw_request || result.raw_response) && (
        <>
          <Separator />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowDetails((v) => !v)}
            data-testid="toggle-details"
          >
            {showDetails ? (
              <>
                <ChevronUp className="size-3.5" /> Hide Details
              </>
            ) : (
              <>
                <ChevronDown className="size-3.5" /> Show Details
              </>
            )}
          </Button>
          {showDetails && <DetailsPanel result={result} />}
        </>
      )}
    </div>
  )
}

// -- Subcomponents ----------------------------------------------------------

function SuccessCard({
  result,
  modelLabel,
}: {
  result: ModelTestResponse
  modelLabel?: string
}) {
  const cost = formatUsd(result.estimated_cost_usd)
  const tokens =
    result.tokens_in !== null || result.tokens_out !== null
      ? `${result.tokens_in ?? 0} in / ${result.tokens_out ?? 0} out`
      : null

  return (
    <div
      className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 dark:border-emerald-500/30 dark:bg-emerald-500/10"
      data-testid="connection-success"
    >
      <div className="flex items-start gap-3">
        <CheckCircle2 className="size-6 shrink-0 text-emerald-600 dark:text-emerald-400" />
        <div className="min-w-0 flex-1 space-y-2">
          <p className="text-sm font-semibold text-emerald-900 dark:text-emerald-100">
            연결 성공{modelLabel ? ` — ${modelLabel}` : ''}
          </p>

          {result.response && (
            <p
              className="line-clamp-3 text-xs text-emerald-900/80 dark:text-emerald-100/80"
              title={result.response}
            >
              응답: {result.response}
            </p>
          )}

          <ul className="grid grid-cols-1 gap-1 text-[11px] text-emerald-900/90 sm:grid-cols-3 dark:text-emerald-100/90">
            <li>
              <span className="font-medium">Latency:</span>{' '}
              {result.latency_ms.toLocaleString()} ms
            </li>
            {tokens && (
              <li>
                <span className="font-medium">Tokens:</span> {tokens}
              </li>
            )}
            {cost && (
              <li>
                <span className="font-medium">Cost:</span> {cost}
              </li>
            )}
          </ul>
        </div>
      </div>
    </div>
  )
}

function ErrorCard({
  result,
  onRetry,
}: {
  result: ModelTestResponse
  onRetry: () => void
}) {
  const kindLabel = result.error
    ? ERROR_KIND_LABEL[result.error.kind] ?? '오류'
    : '오류'
  const message = result.error?.message ?? 'Unknown error'

  return (
    <div
      className="rounded-lg border border-destructive/30 bg-destructive/5 p-4"
      data-testid="connection-error"
    >
      <div className="flex items-start gap-3">
        <AlertCircle className="size-6 shrink-0 text-destructive" />
        <div className="min-w-0 flex-1 space-y-2">
          <p className="text-sm font-semibold text-destructive">{kindLabel}</p>
          <p className="line-clamp-2 text-xs text-destructive/90" title={message}>
            {message}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={onRetry}
            className="text-destructive"
          >
            <RotateCw className="size-3.5" />
            Retry
          </Button>
        </div>
      </div>
    </div>
  )
}

function DetailsPanel({ result }: { result: ModelTestResponse }) {
  return (
    <Tabs defaultValue="request" className="w-full">
      <TabsList>
        <TabsTrigger value="request">Request</TabsTrigger>
        <TabsTrigger value="response">Response</TabsTrigger>
        <TabsTrigger value="curl">Curl</TabsTrigger>
      </TabsList>

      <TabsContent value="request" className="pt-2">
        <CodeBlock
          code={
            result.raw_request
              ? JSON.stringify(maskSensitive(result.raw_request), null, 2)
              : '(no request data)'
          }
        />
      </TabsContent>

      <TabsContent value="response" className="pt-2">
        <CodeBlock
          code={
            result.raw_response
              ? JSON.stringify(result.raw_response, null, 2)
              : '(no response data)'
          }
        />
      </TabsContent>

      <TabsContent value="curl" className="pt-2">
        <div className="space-y-2">
          <CodeBlock code={result.curl_command ?? '(no curl command)'} />
          {result.curl_command && (
            <Button
              variant="outline"
              size="sm"
              onClick={async () => {
                await navigator.clipboard.writeText(result.curl_command ?? '')
                toast.success('Copied to clipboard')
              }}
              data-testid="copy-curl"
            >
              <Copy className="size-3.5" /> Copy
            </Button>
          )}
        </div>
      </TabsContent>
    </Tabs>
  )
}

function CodeBlock({ code }: { code: string }) {
  return (
    <pre className="max-h-64 overflow-auto rounded border bg-muted p-3 font-mono text-[11px] leading-relaxed">
      {code}
    </pre>
  )
}

// -- Helpers ----------------------------------------------------------------

function formatUsd(value: number | null | undefined): string | null {
  if (value === null || value === undefined) return null
  if (!Number.isFinite(value)) return null
  // Tiny costs use 6 decimals; bigger ones get 4 to stay readable.
  const decimals = Math.abs(value) < 0.01 ? 6 : 4
  return `$${value.toFixed(decimals)}`
}

/**
 * Defense-in-depth: backend already masks Authorization headers, but we
 * re-mask on the way to the screen in case a misconfigured backend response
 * leaks a bearer token. We never want to render an API key cleartext.
 */
function maskSensitive(req: { headers: Record<string, string>; body: unknown; url: string; method: string }) {
  const headers: Record<string, string> = {}
  for (const [k, v] of Object.entries(req.headers ?? {})) {
    headers[k] = isSensitiveHeader(k) ? maskValue(v) : v
  }
  return { ...req, headers }
}

function isSensitiveHeader(name: string): boolean {
  const lower = name.toLowerCase()
  return (
    lower === 'authorization' ||
    lower === 'x-api-key' ||
    lower === 'api-key' ||
    lower === 'x-goog-api-key' ||
    lower.endsWith('-token')
  )
}

function maskValue(value: string): string {
  if (!value) return value
  if (/^bearer\s+/i.test(value)) {
    const token = value.replace(/^bearer\s+/i, '')
    return `Bearer ${maskToken(token)}`
  }
  return maskToken(value)
}

function maskToken(token: string): string {
  if (token.length <= 8) return '*'.repeat(token.length)
  return `${token.slice(0, 4)}${'*'.repeat(Math.max(token.length - 8, 4))}${token.slice(-4)}`
}
