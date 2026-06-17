type ClientLogDetail = unknown

function hasDetail(detail: ClientLogDetail | undefined): detail is ClientLogDetail {
  return detail !== undefined
}

export function reportClientWarning(
  scope: string,
  message: string,
  detail?: ClientLogDetail,
): void {
  const text = `[${scope}] ${message}`
  if (hasDetail(detail)) {
    console.warn(text, detail)
    return
  }
  console.warn(text)
}

export function reportClientError(scope: string, message: string, detail?: ClientLogDetail): void {
  const text = `[${scope}] ${message}`
  if (hasDetail(detail)) {
    console.error(text, detail)
    return
  }
  console.error(text)
}
