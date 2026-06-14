export class MissingQueryIdError extends Error {
  constructor(readonly fieldName: string) {
    super(`${fieldName} is required before running this query.`)
    this.name = 'MissingQueryIdError'
  }
}

export function requireQueryId(value: string | null | undefined, fieldName: string): string {
  if (!value) {
    throw new MissingQueryIdError(fieldName)
  }
  return value
}
