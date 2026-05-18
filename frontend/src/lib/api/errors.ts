/**
 * Backend error envelope helpers.
 *
 * Every non-2xx the FastAPI app emits is either ``{error: {code, message}}``
 * (the AppError shape) or ``{detail: {code, message}}`` (FastAPI's validation
 * default). A few legacy routes still flatten the fields onto the root
 * (``{code, message}``). ``readApiErrorBody`` handles the three shapes in
 * one place so JSON callers and SSE callers can't drift on the parsing
 * priority.
 *
 * ``ApiError`` is the structured throwable both layers use — and
 * ``StreamApiError`` extends it so ``instanceof ApiError`` recognises both
 * REST and SSE failures.
 */

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

type ApiErrorBodyShape = {
  error?: { code?: string; message?: string }
  detail?: { code?: string; message?: string } | string
  code?: string
  message?: string
}

interface ReadOptions {
  /** Returned when the body has no recognised ``code``. */
  fallbackCode: string
  /** Returned when the body has no recognised ``message``. Defaults to
   *  ``response.statusText`` so JSON callers preserve the existing
   *  ``"Internal Server Error"``-style fallback. */
  fallbackMessage?: string
  /** ``true`` ⇒ read via ``response.clone()`` so the original body stream
   *  is left for a downstream consumer (SSE parser does this). */
  clone?: boolean
}

/** Parse a non-2xx response body into ``{code, message}``.
 *
 *  Priority: ``body.error`` → ``body.detail`` (object) → ``body.detail``
 *  (string, message only) → root ``body`` fields → ``fallback*``. Never
 *  throws — JSON parse failures degrade to the fallbacks.
 */
export async function readApiErrorBody(
  response: Response,
  { fallbackCode, fallbackMessage, clone = false }: ReadOptions,
): Promise<{ code: string; message: string }> {
  const source = clone ? response.clone() : response
  const body = (await source.json().catch(() => ({}))) as ApiErrorBodyShape

  const detail = body.error ?? body.detail ?? null
  let code: string | undefined
  let message: string | undefined

  if (detail && typeof detail === 'object') {
    code = detail.code
    message = detail.message
  } else if (typeof detail === 'string') {
    message = detail
  }

  return {
    code: code ?? body.code ?? fallbackCode,
    message: message ?? body.message ?? fallbackMessage ?? response.statusText,
  }
}

/** Shortcut: parse + throw an ``ApiError``. Used by JSON callers; SSE
 *  callers want the raw fields to feed a ``StreamApiError`` subclass. */
export async function throwApiError(
  response: Response,
  fallbackCode: string,
): Promise<never> {
  const { code, message } = await readApiErrorBody(response, { fallbackCode })
  throw new ApiError(response.status, code, message)
}
