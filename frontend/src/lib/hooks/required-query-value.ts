export function requiredQueryValue(value: string | null | undefined, label: string): string {
  if (value) return value
  throw new Error(`${label} is required before running the query`)
}
