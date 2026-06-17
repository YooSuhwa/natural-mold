import type {
  SkillBuilderSession,
  SkillCompatibilityResult,
  SkillDraftFile,
  SkillDraftPackage,
} from '@/lib/types'

export type FileChangeKind = 'added' | 'changed' | 'deleted'

export type FileChange = {
  readonly path: string
  readonly kind: FileChangeKind
}

export type FileDiffSummary = {
  readonly originalCount: number
  readonly proposedCount: number
  readonly added: number
  readonly changed: number
  readonly deleted: number
  readonly files: readonly FileChange[]
}

export type ValidationSeverity = 'error' | 'warning' | 'info'

export type ValidationIssueView = {
  readonly severity: ValidationSeverity
  readonly message: string
}

export type ChangelogView = {
  readonly summary: string | null
  readonly items: readonly string[]
}

export type BenchmarkView = {
  readonly passRate: number | null
  readonly meanScore: number | null
  readonly delta: number | null
}

export type CompatibilityResultSource =
  | SkillCompatibilityResult
  | Readonly<Record<string, unknown>>
  | null

type SnapshotFile = {
  readonly path: string
  readonly content: string
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function compactValue(value: unknown): string {
  if (value === null || value === undefined) {
    return ''
  }
  if (typeof value === 'string') {
    return value
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  return 'object'
}

function snapshotFiles(session: SkillBuilderSession | null): readonly SnapshotFile[] {
  const snapshot = session?.base_snapshot
  if (!isRecord(snapshot) || !Array.isArray(snapshot.files)) {
    return []
  }
  return snapshot.files.flatMap((value) => {
    if (!isRecord(value)) {
      return []
    }
    const path = stringValue(value.path)
    if (!path) {
      return []
    }
    return [{ path, content: stringValue(value.content) ?? '' }]
  })
}

function draftFile(file: SkillDraftFile): SnapshotFile {
  return { path: file.path, content: file.content }
}

export function fileDiffSummary(
  session: SkillBuilderSession | null,
  draft: SkillDraftPackage,
): FileDiffSummary | null {
  if (session?.mode !== 'improve') {
    return null
  }
  const original = snapshotFiles(session)
  if (original.length === 0) {
    return null
  }
  const proposed = draft.files.map(draftFile)
  const originalByPath = new Map(original.map((file) => [file.path, file.content]))
  const proposedByPath = new Map(proposed.map((file) => [file.path, file.content]))
  const changedAndAdded = proposed.flatMap((file): readonly FileChange[] => {
    const previous = originalByPath.get(file.path)
    if (previous === undefined) {
      return [{ path: file.path, kind: 'added' }]
    }
    if (previous !== file.content) {
      return [{ path: file.path, kind: 'changed' }]
    }
    return []
  })
  const deleted = original.flatMap((file): readonly FileChange[] =>
    proposedByPath.has(file.path) ? [] : [{ path: file.path, kind: 'deleted' }],
  )
  const files = [...changedAndAdded, ...deleted]
  return {
    originalCount: original.length,
    proposedCount: proposed.length,
    added: files.filter((file) => file.kind === 'added').length,
    changed: files.filter((file) => file.kind === 'changed').length,
    deleted: files.filter((file) => file.kind === 'deleted').length,
    files,
  }
}

function normalizeSeverity(value: unknown): ValidationSeverity {
  switch (stringValue(value)) {
    case 'error':
      return 'error'
    case 'warning':
      return 'warning'
    case 'info':
      return 'info'
    default:
      return 'info'
  }
}

function validationIssue(value: unknown): ValidationIssueView | null {
  if (!isRecord(value)) {
    const message = compactValue(value)
    return message ? { severity: 'info', message } : null
  }
  const message = stringValue(value.message) ?? stringValue(value.summary) ?? compactValue(value)
  if (!message) {
    return null
  }
  const path = stringValue(value.path)
  return {
    severity: normalizeSeverity(value.severity),
    message: path ? `${path}: ${message}` : message,
  }
}

export function validationIssueViews(
  session: SkillBuilderSession | null,
  draft: SkillDraftPackage,
): readonly ValidationIssueView[] {
  const resultIssues = session?.validation_result?.issues
  const source = Array.isArray(resultIssues) ? resultIssues : draft.validation_issues
  return source.flatMap((item) => {
    const issue = validationIssue(item)
    return issue ? [issue] : []
  })
}

export function changelogView(
  session: SkillBuilderSession | null,
  draft: SkillDraftPackage,
): ChangelogView | null {
  const source = session?.changelog_draft ?? draft.changelog_draft
  if (!isRecord(source)) {
    return null
  }
  const summary = stringValue(source.summary) ?? stringValue(source.title)
  const items = Array.isArray(source.items)
    ? source.items.flatMap((item) => {
        const label = changelogItemLabel(item)
        return label ? [label] : []
      })
    : []
  return summary || items.length > 0 ? { summary, items } : null
}

export function compatibilityResult(
  session: SkillBuilderSession | null,
  draft: SkillDraftPackage,
): CompatibilityResultSource {
  return session?.compatibility_result ?? draft.compatibility_result ?? null
}

export function compatibilityTargetKeys(result: CompatibilityResultSource): readonly string[] {
  if (!isRecord(result)) {
    return []
  }
  const targets = result['targets']
  return isRecord(targets) ? Object.keys(targets) : []
}

function changelogItemLabel(value: unknown): string | null {
  if (!isRecord(value)) {
    return stringValue(value)
  }
  const message =
    stringValue(value.title) ?? stringValue(value.summary) ?? stringValue(value.message)
  if (!message) {
    return null
  }
  const path = stringValue(value.path)
  return path ? `${path}: ${message}` : message
}

export function benchmarkView(
  session: SkillBuilderSession | null,
  draft: SkillDraftPackage,
): BenchmarkView | null {
  const source = draft.benchmark ?? benchmarkSource(session?.eval_result)
  if (!isRecord(source)) {
    return null
  }
  const view = {
    passRate: numberValue(source.pass_rate),
    meanScore: numberValue(source.mean_score),
    delta: numberValue(source.delta),
  }
  return view.passRate !== null || view.meanScore !== null || view.delta !== null ? view : null
}

function benchmarkSource(value: unknown): unknown {
  if (!isRecord(value)) {
    return null
  }
  return isRecord(value.benchmark) ? value.benchmark : value
}
