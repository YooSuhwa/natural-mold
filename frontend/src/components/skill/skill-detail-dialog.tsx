'use client'

import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { Download, FilePlus2, Loader2, Save, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { DialogShell } from '@/components/shared/dialog-shell'
import { DeleteConfirmInline } from '@/components/shared/delete-confirm-inline'
import { SkillPackageTree } from './skill-package-tree'
import {
  useDeleteSkill,
  useDeleteSkillFile,
  useSetSkillFile,
  useSkill,
  useSkillContent,
  useSkillFiles,
  useUpdateSkillContent,
} from '@/lib/hooks/use-skills'
import { skillsApi } from '@/lib/api/skills'

interface Props {
  skillId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function SkillDetailDialog({ skillId, open, onOpenChange }: Props) {
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="xl" height="tall">
      {skillId ? (
        // Re-key on `skillId` so each new selection gets fresh local state.
        <SkillDetailBody key={skillId} skillId={skillId} onClose={() => onOpenChange(false)} />
      ) : (
        <>
          <DialogShell.Header title="Loading skill…" />
          <DialogShell.Body>
            <Skeleton className="h-40 w-full rounded-lg" />
          </DialogShell.Body>
          <DialogShell.Footer>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Close
            </Button>
          </DialogShell.Footer>
        </>
      )}
    </DialogShell>
  )
}

function SkillDetailBody({ skillId, onClose }: { skillId: string; onClose: () => void }) {
  const { data: skill } = useSkill(skillId)
  const isText = skill?.kind === 'text'
  const isPackage = skill?.kind === 'package'

  if (!skill) {
    return (
      <>
        <DialogShell.Header title="Loading skill…" />
        <DialogShell.Body>
          <Skeleton className="h-40 w-full rounded-lg" />
        </DialogShell.Body>
        <DialogShell.Footer>
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
        </DialogShell.Footer>
      </>
    )
  }

  const header = (
    <DialogShell.Header
      title={
        <span className="inline-flex items-center gap-2">
          {skill.name}
          <Badge variant="secondary" className="text-[10px]">
            {skill.kind}
          </Badge>
        </span>
      }
      description={skill.description ?? skill.slug}
    />
  )

  if (isText) {
    return (
      <>
        {header}
        <TextSkillEditor skillId={skillId} onClose={onClose} />
      </>
    )
  }

  if (isPackage) {
    return (
      <>
        {header}
        <PackageSkillEditor skillId={skillId} onClose={onClose} />
      </>
    )
  }

  return (
    <>
      {header}
      <DialogShell.Body>
        <p className="text-sm text-muted-foreground">Unsupported skill kind.</p>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          Close
        </Button>
      </DialogShell.Footer>
    </>
  )
}

// ─────────────────────────────────────────────────────────────────────────
// Text skill — single textarea (legacy behavior preserved)
// ─────────────────────────────────────────────────────────────────────────

function TextSkillEditor({
  skillId,
  onClose,
}: {
  skillId: string
  onClose: () => void
}) {
  const { data: textContent } = useSkillContent(skillId, true)
  const update = useUpdateSkillContent()
  const remove = useDeleteSkill()
  const [editor, setEditor] = useState('')
  const [hydrated, setHydrated] = useState(false)
  const [confirming, setConfirming] = useState(false)

  // Hydrate editor when content arrives. Avoid the React 19 setState-in-effect
  // anti-pattern by gating with `hydrated`.
  if (!hydrated && textContent?.content !== undefined) {
    setHydrated(true)
    setEditor(textContent.content)
  }

  async function handleSave() {
    try {
      await update.mutateAsync({ id: skillId, data: { content: editor } })
      toast.success('Saved')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed')
    }
  }

  async function handleDelete() {
    try {
      await remove.mutateAsync(skillId)
      toast.success('Skill deleted')
      onClose()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  return (
    <>
      <DialogShell.Body>
        <Textarea
          value={editor}
          rows={20}
          className="h-full min-h-[400px] font-mono text-xs"
          onChange={(e) => setEditor(e.target.value)}
        />
      </DialogShell.Body>
      <DialogShell.Footer>
        {confirming ? (
          <div className="flex-1">
            <DeleteConfirmInline
              entity="skill"
              onCancel={() => setConfirming(false)}
              onConfirm={handleDelete}
              pending={remove.isPending}
            />
          </div>
        ) : (
          <Button
            variant="ghost"
            size="sm"
            className="mr-auto text-destructive hover:bg-destructive/10 hover:text-destructive"
            onClick={() => setConfirming(true)}
          >
            <Trash2 className="size-3.5" />
            Delete skill
          </Button>
        )}
        <Button variant="outline" onClick={onClose}>
          Close
        </Button>
        <Button onClick={handleSave} disabled={update.isPending}>
          {update.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Save className="size-4" />
          )}
          Save
        </Button>
      </DialogShell.Footer>
    </>
  )
}

// ─────────────────────────────────────────────────────────────────────────
// Package skill — split layout: tree sidebar + per-file editor
// ─────────────────────────────────────────────────────────────────────────

const TEXT_EXTENSIONS = new Set([
  'md',
  'markdown',
  'txt',
  'py',
  'js',
  'jsx',
  'ts',
  'tsx',
  'json',
  'yaml',
  'yml',
  'toml',
  'css',
  'html',
  'sh',
  'bash',
  'zsh',
  'sql',
  'env',
  'gitignore',
  'rst',
  'log',
])

const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp'])

function getExt(path: string): string {
  const i = path.lastIndexOf('.')
  if (i === -1) return ''
  return path.slice(i + 1).toLowerCase()
}

function isTextFile(path: string): boolean {
  const ext = getExt(path)
  if (!ext) return true // no extension → assume text (e.g. README, Makefile)
  return TEXT_EXTENSIONS.has(ext)
}

function isImageFile(path: string): boolean {
  return IMAGE_EXTENSIONS.has(getExt(path))
}

function isPdf(path: string): boolean {
  return getExt(path) === 'pdf'
}

function PackageSkillEditor({
  skillId,
  onClose,
}: {
  skillId: string
  onClose: () => void
}) {
  const { data: skill } = useSkill(skillId)
  const { data: files } = useSkillFiles(skillId)
  const setFile = useSetSkillFile(skillId)
  const deleteFile = useDeleteSkillFile(skillId)
  const removeSkill = useDeleteSkill()

  const fileEntries = useMemo(
    () => (files ?? []).filter((f) => !f.is_dir),
    [files],
  )

  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  // Map<path, current-content> — only paths with unsaved edits live here.
  const [drafts, setDrafts] = useState<Map<string, string>>(new Map())
  // Per-path remote content cache (loaded text only). Keep in component state
  // so we don't re-fetch on every keystroke.
  const [remoteCache, setRemoteCache] = useState<Map<string, string>>(new Map())
  const [confirmingSkillDelete, setConfirmingSkillDelete] = useState(false)
  const [confirmingFileDelete, setConfirmingFileDelete] = useState(false)
  const [adding, setAdding] = useState(false)
  const [newPath, setNewPath] = useState('')

  // Default selection: SKILL.md (or first text file) once files load.
  if (selectedPath === null && fileEntries.length > 0) {
    const skillMd = fileEntries.find((f) => f.path.endsWith('SKILL.md'))
    setSelectedPath(skillMd?.path ?? fileEntries[0].path)
  }

  // Lazy-load remote content for the selected text file. All setState calls
  // happen inside async callbacks (which is fine for the React 19 lint rule);
  // the effect body itself only kicks off the fetch.
  useEffect(() => {
    if (!selectedPath) return
    if (!isTextFile(selectedPath)) return
    if (remoteCache.has(selectedPath)) return
    let cancelled = false
    fetch(skillsApi.fileUrl(skillId, selectedPath))
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((body) => {
        if (cancelled) return
        setRemoteCache((prev) => {
          const next = new Map(prev)
          next.set(selectedPath, body)
          return next
        })
      })
      .catch((e) => {
        if (cancelled) return
        toast.error(`Load failed: ${e instanceof Error ? e.message : 'unknown'}`)
      })
    return () => {
      cancelled = true
    }
  }, [skillId, selectedPath, remoteCache])

  // Derived loading flag — true while the selected text file is fetching.
  const loadingFile =
    !!selectedPath && isTextFile(selectedPath) && !remoteCache.has(selectedPath)

  const dirtyPaths = useMemo(() => new Set(drafts.keys()), [drafts])
  const isDirty = (path: string) => dirtyPaths.has(path)
  const currentContent = selectedPath
    ? drafts.get(selectedPath) ?? remoteCache.get(selectedPath) ?? ''
    : ''
  const currentDirty = selectedPath ? isDirty(selectedPath) : false
  const isSkillMd = selectedPath?.endsWith('SKILL.md') ?? false

  function selectFile(path: string) {
    if (path === selectedPath) return
    if (currentDirty) {
      const ok = window.confirm(
        `Discard unsaved changes to ${selectedPath}?\n\nClick OK to discard, Cancel to keep editing.`,
      )
      if (!ok) return
      // Drop the draft for the previously-selected path.
      setDrafts((prev) => {
        const next = new Map(prev)
        if (selectedPath) next.delete(selectedPath)
        return next
      })
    }
    setSelectedPath(path)
    setConfirmingFileDelete(false)
  }

  function handleEdit(value: string) {
    if (!selectedPath) return
    const remote = remoteCache.get(selectedPath) ?? ''
    setDrafts((prev) => {
      const next = new Map(prev)
      if (value === remote) {
        next.delete(selectedPath)
      } else {
        next.set(selectedPath, value)
      }
      return next
    })
  }

  async function handleSave() {
    if (!selectedPath || !currentDirty) return
    const value = drafts.get(selectedPath) ?? ''
    try {
      await setFile.mutateAsync({ path: selectedPath, content: value })
      // Promote draft to remote cache, drop the draft entry.
      setRemoteCache((prev) => {
        const next = new Map(prev)
        next.set(selectedPath, value)
        return next
      })
      setDrafts((prev) => {
        const next = new Map(prev)
        next.delete(selectedPath)
        return next
      })
      toast.success(`Saved ${selectedPath}`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed')
    }
  }

  async function handleDeleteFile() {
    if (!selectedPath) return
    try {
      await deleteFile.mutateAsync(selectedPath)
      // Drop the deleted path from caches.
      setRemoteCache((prev) => {
        const next = new Map(prev)
        next.delete(selectedPath)
        return next
      })
      setDrafts((prev) => {
        const next = new Map(prev)
        next.delete(selectedPath)
        return next
      })
      toast.success(`Deleted ${selectedPath}`)
      setConfirmingFileDelete(false)
      setSelectedPath(null) // re-default to SKILL.md on next render
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  async function handleAddFile() {
    const trimmed = newPath.trim().replace(/^\/+/, '')
    if (!trimmed) {
      toast.error('Path required')
      return
    }
    if (fileEntries.some((f) => f.path === trimmed)) {
      toast.error('File already exists')
      return
    }
    try {
      await setFile.mutateAsync({ path: trimmed, content: '' })
      toast.success(`Created ${trimmed}`)
      setNewPath('')
      setAdding(false)
      // Seed remote cache so the editor opens with the empty content immediately.
      setRemoteCache((prev) => {
        const next = new Map(prev)
        next.set(trimmed, '')
        return next
      })
      setSelectedPath(trimmed)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Create failed')
    }
  }

  async function handleDeleteSkill() {
    try {
      await removeSkill.mutateAsync(skillId)
      toast.success('Skill deleted')
      onClose()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed')
    }
  }

  return (
    <>
      <DialogShell.Split>
        <DialogShell.Sidebar className="w-[260px]">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Files
            </h3>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2 text-xs"
              onClick={() => setAdding((v) => !v)}
              aria-label="Add file"
            >
              <FilePlus2 className="size-3.5" />
              Add
            </Button>
          </div>
          {adding ? (
            <div className="mb-3 space-y-1.5 rounded-md border border-border/60 bg-background p-2">
              <Input
                value={newPath}
                onChange={(e) => setNewPath(e.target.value)}
                placeholder="path/to/new-file.md"
                className="h-7 text-xs"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleAddFile()
                  if (e.key === 'Escape') {
                    setAdding(false)
                    setNewPath('')
                  }
                }}
                autoFocus
              />
              <div className="flex gap-1">
                <Button
                  size="sm"
                  className="h-6 flex-1 text-[11px]"
                  onClick={handleAddFile}
                  disabled={setFile.isPending || !newPath.trim()}
                >
                  Create
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-6 flex-1 text-[11px]"
                  onClick={() => {
                    setAdding(false)
                    setNewPath('')
                  }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : null}
          <SkillPackageTree
            files={files ?? []}
            selectedPath={selectedPath}
            onSelect={selectFile}
          />
          {dirtyPaths.size > 0 ? (
            <p className="mt-3 text-[10px] text-muted-foreground">
              {dirtyPaths.size} unsaved file{dirtyPaths.size > 1 ? 's' : ''}
            </p>
          ) : null}
        </DialogShell.Sidebar>

        <DialogShell.Body className="flex flex-col gap-3 py-4">
          <FileEditorPane
            skillId={skillId}
            selectedPath={selectedPath}
            currentContent={currentContent}
            currentDirty={currentDirty}
            isSkillMd={isSkillMd}
            loadingFile={loadingFile}
            confirmingFileDelete={confirmingFileDelete}
            deletePending={deleteFile.isPending}
            onEdit={handleEdit}
            onAskDelete={() => setConfirmingFileDelete(true)}
            onCancelDelete={() => setConfirmingFileDelete(false)}
            onConfirmDelete={handleDeleteFile}
          />
        </DialogShell.Body>
      </DialogShell.Split>

      <DialogShell.Footer>
        {confirmingSkillDelete ? (
          <div className="flex-1">
            <DeleteConfirmInline
              entity="skill (entire package)"
              onCancel={() => setConfirmingSkillDelete(false)}
              onConfirm={handleDeleteSkill}
              pending={removeSkill.isPending}
            />
          </div>
        ) : (
          <Button
            variant="ghost"
            size="sm"
            className="mr-auto text-destructive hover:bg-destructive/10 hover:text-destructive"
            onClick={() => setConfirmingSkillDelete(true)}
          >
            <Trash2 className="size-3.5" />
            Delete skill
          </Button>
        )}
        <span className="text-[10px] text-muted-foreground">
          {skill?.size_bytes ?? 0}b · v{skill?.version ?? '—'} · used by{' '}
          {skill?.used_by_count ?? 0}
        </span>
        <Button variant="outline" onClick={onClose}>
          Close
        </Button>
        <Button
          onClick={handleSave}
          disabled={!currentDirty || setFile.isPending}
        >
          {setFile.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Save className="size-4" />
          )}
          Save file
        </Button>
      </DialogShell.Footer>
    </>
  )
}

// ─────────────────────────────────────────────────────────────────────────
// Right-pane editor — text / image / pdf / binary preview
// ─────────────────────────────────────────────────────────────────────────

interface FileEditorPaneProps {
  skillId: string
  selectedPath: string | null
  currentContent: string
  currentDirty: boolean
  isSkillMd: boolean
  loadingFile: boolean
  confirmingFileDelete: boolean
  deletePending: boolean
  onEdit: (value: string) => void
  onAskDelete: () => void
  onCancelDelete: () => void
  onConfirmDelete: () => void
}

function FileEditorPane({
  skillId,
  selectedPath,
  currentContent,
  currentDirty,
  isSkillMd,
  loadingFile,
  confirmingFileDelete,
  deletePending,
  onEdit,
  onAskDelete,
  onCancelDelete,
  onConfirmDelete,
}: FileEditorPaneProps) {
  if (!selectedPath) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Pick a file from the sidebar to begin editing.
      </div>
    )
  }

  return (
    <>
      <div className="flex items-center gap-2">
        <code className="rounded bg-muted/50 px-2 py-0.5 font-mono text-[11px]">
          {selectedPath}
        </code>
        {currentDirty ? (
          <Badge
            variant="secondary"
            className="bg-status-warn/15 text-[10px] text-status-warn"
          >
            • unsaved
          </Badge>
        ) : null}
        {!isSkillMd ? (
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto h-7 px-2 text-xs text-destructive hover:bg-destructive/10 hover:text-destructive"
            onClick={onAskDelete}
          >
            <Trash2 className="size-3.5" />
            Delete file
          </Button>
        ) : (
          <Badge
            variant="secondary"
            className="ml-auto text-[10px] text-muted-foreground"
          >
            protected
          </Badge>
        )}
      </div>

      {confirmingFileDelete ? (
        <DeleteConfirmInline
          entity="file"
          onCancel={onCancelDelete}
          onConfirm={onConfirmDelete}
          pending={deletePending}
        />
      ) : null}

      <FileContentArea
        skillId={skillId}
        path={selectedPath}
        content={currentContent}
        loading={loadingFile}
        onEdit={onEdit}
      />
    </>
  )
}

function FileContentArea({
  skillId,
  path,
  content,
  loading,
  onEdit,
}: {
  skillId: string
  path: string
  content: string
  loading: boolean
  onEdit: (value: string) => void
}) {
  if (isImageFile(path)) {
    return (
      <div className="flex flex-1 flex-col items-center gap-3 overflow-auto rounded-md border border-border/60 bg-muted/20 p-4">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={skillsApi.fileUrl(skillId, path)}
          alt={path}
          className="max-h-[420px] max-w-full rounded shadow-sm"
        />
        <a
          href={skillsApi.fileUrl(skillId, path)}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-xs text-primary-strong hover:underline"
        >
          <Download className="size-3" /> Open original
        </a>
      </div>
    )
  }

  if (isPdf(path)) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-md border border-border/60 bg-muted/20 p-6">
        <p className="text-sm font-medium">{path}</p>
        <a
          href={skillsApi.fileUrl(skillId, path)}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-background px-3 py-1.5 text-xs hover:bg-muted"
        >
          <Download className="size-3.5" /> Open PDF
        </a>
      </div>
    )
  }

  if (!isTextFile(path)) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-md border border-border/60 bg-muted/20 p-6 text-center">
        <p className="text-sm font-medium">Binary file</p>
        <p className="text-xs text-muted-foreground">
          Editor preview is not available for this file type.
        </p>
        <a
          href={skillsApi.fileUrl(skillId, path)}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-background px-3 py-1.5 text-xs hover:bg-muted"
        >
          <Download className="size-3.5" /> Download
        </a>
      </div>
    )
  }

  if (loading) {
    return <Skeleton className="h-full min-h-[280px] flex-1 rounded-md" />
  }

  return (
    <Textarea
      value={content}
      onChange={(e) => onEdit(e.target.value)}
      className="h-full min-h-[280px] flex-1 resize-none font-mono text-xs"
    />
  )
}
