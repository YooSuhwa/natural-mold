const EMAIL = /^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/

export interface ParsedEmails {
  valid: string[]
  invalid: string[]
}

/** S11: split on commas, semicolons or line breaks; trim, lowercase and de-duplicate. */
export function parseInviteEmails(input: string): ParsedEmails {
  const seen = new Set<string>()
  const valid: string[] = []
  const invalid: string[] = []
  for (const raw of input.split(/[\s,;]+/)) {
    const email = raw.trim().toLowerCase()
    if (!email || seen.has(email)) continue
    seen.add(email)
    if (EMAIL.test(email)) valid.push(email)
    else invalid.push(email)
  }
  return { valid, invalid }
}

/** S12 accept route (`/invite/[token]`); the token is a one-time secret. */
export function invitationLink(token: string, origin: string): string {
  return `${origin}/invite/${encodeURIComponent(token)}`
}

export async function copyText(text: string): Promise<boolean> {
  try {
    if (!navigator.clipboard?.writeText) return false
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
