'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Download, FilePlus2, Loader2, Save, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { DialogShell } from '@/components/shared/dialog-shell'
import { DomainIconTile, getDomainIconIdForSkillKind } from '@/components/shared/icon'
import { DeleteConfirmInline } from '@/components/shared/delete-confirm-inline'
import { CredentialPicker } from '@/components/credential/credential-picker'
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
import {
  useDeleteSkillCredentialBinding,
  useSetSkillCredentialBinding,
  useSkillCredentialBindings,
  useSkillCredentialRequirements,
} from '@/lib/hooks/use-marketplace'
import { skillsApi } from '@/lib/api/skills'

interface Props {
  skillId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function SkillDetailDialog({ skillId, open, onOpenChange }: Props) {
  const t = useTranslations('skill.detailDialog')
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="xl" height="tall">
      {skillId ? (
        // Re-key on `skillId` so each new selection gets fresh local state.
        <SkillDetailBody key={skillId} skillId={skillId} onClose={() => onOpenChange(false)} />
      ) : (
        <>
          <DialogShell.Header title={t('loading')} />
          <DialogShell.Body>
            <Skeleton className="h-40 w-full rounded-lg" />
          </DialogShell.Body>
          <DialogShell.Footer>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              {t('close')}
            </Button>
          </DialogShell.Footer>
        </>
      )}
    </DialogShell>
  )
}

function SkillDetailBody({ skillId, onClose }: { skillId: string; onClose: () => void }) {
  const t = useTranslations('skill.detailDialog')
  const { data: skill } = useSkill(skillId)
  const isText = skill?.kind === 'text'
  const isPackage = skill?.kind === 'package'

  if (!skill) {
    return (
      <>
        <DialogShell.Header title={t('loading')} />
        <DialogShell.Body>
          <Skeleton className="h-40 w-full rounded-lg" />
        </DialogShell.Body>
        <DialogShell.Footer>
          <Button variant="outline" onClick={onClose}>
            {t('close')}
          </Button>
        </DialogShell.Footer>
      </>
    )
  }

  const header = (
    <DialogShell.Header
      icon={
        <DomainIconTile
          iconId={getDomainIconIdForSkillKind(skill.kind)}
          className="size-9"
          iconClassName="size-5"
        />
      }
      title={
        <span className="inline-flex items-center gap-2">
          {skill.name}
          <Badge variant="secondary" className="moldy-ui-micro">
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
        <p className="text-sm text-muted-foreground">{t('unsupported')}</p>
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {t('close')}
        </Button>
      </DialogShell.Footer>
    </>
  )
}

// ─────────────────────────────────────────────────────────────────────────
// Text skill — single textarea (legacy behavior preserved)
// ─────────────────────────────────────────────────────────────────────────

function TextSkillEditor({ skillId, onClose }: { skillId: string; onClose: () => void }) {
  const t = useTranslations('skill.detailDialog')
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
      toast.success(t('saved'))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('saveFailed'))
    }
  }

  async function handleDelete() {
    try {
      await remove.mutateAsync(skillId)
      toast.success(t('deleted'))
      onClose()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('deleteFailed'))
    }
  }

  return (
    <>
      <DialogShell.Body>
        <SkillCredentialBindingsPanel skillId={skillId} />
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
              entity={t('skillEntity')}
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
            {t('deleteSkill')}
          </Button>
        )}
        <Button variant="outline" onClick={onClose}>
          {t('close')}
        </Button>
        <Button onClick={handleSave} disabled={update.isPending}>
          {update.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Save className="size-4" />
          )}
          {t('save')}
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

function PackageSkillEditor({ skillId, onClose }: { skillId: string; onClose: () => void }) {
  const t = useTranslations('skill.detailDialog')
  const { data: skill } = useSkill(skillId)
  const { data: files } = useSkillFiles(skillId)
  const setFile = useSetSkillFile(skillId)
  const deleteFile = useDeleteSkillFile(skillId)
  const removeSkill = useDeleteSkill()

  const fileEntries = useMemo(() => (files ?? []).filter((f) => !f.is_dir), [files])

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
  //
  // ``remoteCache`` is intentionally excluded from deps: the guard below reads
  // the current closure value of the Map, which is always fresh at the time
  // ``selectedPath`` changes (the only moment a new closure is created).
  // Including ``remoteCache`` would re-run the effect on every cache update,
  // causing unnecessary effect setup/teardown cycles without any benefit.
  useEffect(() => {
    if (!selectedPath) return
    if (!isTextFile(selectedPath)) return
    if (remoteCache.has(selectedPath)) return
    let cancelled = false
    // ``credentials: 'include'`` is required cross-origin (3000 → 8001) so
    // the HttpOnly auth cookies attach — without it the backend treats
    // this as anonymous and returns 401 for the file-read endpoint.
    fetch(skillsApi.fileUrl(skillId, selectedPath), { credentials: 'include' })
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
        toast.error(t('loadFailed', { message: e instanceof Error ? e.message : 'unknown' }))
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skillId, selectedPath])

  // Derived loading flag — true while the selected text file is fetching.
  const loadingFile = !!selectedPath && isTextFile(selectedPath) && !remoteCache.has(selectedPath)

  const dirtyPaths = useMemo(() => new Set(drafts.keys()), [drafts])
  const isDirty = (path: string) => dirtyPaths.has(path)
  const currentContent = selectedPath
    ? (drafts.get(selectedPath) ?? remoteCache.get(selectedPath) ?? '')
    : ''
  const currentDirty = selectedPath ? isDirty(selectedPath) : false
  const isSkillMd = selectedPath?.endsWith('SKILL.md') ?? false

  function selectFile(path: string) {
    if (path === selectedPath) return
    if (currentDirty) {
      const ok = window.confirm(
        t('discardConfirm', { path: selectedPath ?? '' }),
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
      toast.success(t('fileSaved', { path: selectedPath }))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('saveFailed'))
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
      toast.success(t('fileDeleted', { path: selectedPath }))
      setConfirmingFileDelete(false)
      setSelectedPath(null) // re-default to SKILL.md on next render
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('deleteFailed'))
    }
  }

  async function handleAddFile() {
    const trimmed = newPath.trim().replace(/^\/+/, '')
    if (!trimmed) {
      toast.error(t('pathRequired'))
      return
    }
    if (fileEntries.some((f) => f.path === trimmed)) {
      toast.error(t('fileExists'))
      return
    }
    try {
      await setFile.mutateAsync({ path: trimmed, content: '' })
      toast.success(t('fileCreated', { path: trimmed }))
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
      toast.error(e instanceof Error ? e.message : t('createFailed'))
    }
  }

  async function handleDeleteSkill() {
    try {
      await removeSkill.mutateAsync(skillId)
      toast.success(t('deleted'))
      onClose()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('deleteFailed'))
    }
  }

  return (
    <>
      <DialogShell.Split>
        <DialogShell.Sidebar className="w-[260px]">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t('files')}
            </h3>
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2 text-xs"
              onClick={() => setAdding((v) => !v)}
              aria-label={t('addFile')}
            >
              <FilePlus2 className="size-3.5" />
              {t('add')}
            </Button>
          </div>
          {adding ? (
            <div className="mb-3 space-y-1.5 rounded-md border border-border/60 bg-background p-2">
              <Input
                value={newPath}
                onChange={(e) => setNewPath(e.target.value)}
                placeholder={t('newPathPlaceholder')}
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
                  className="h-6 flex-1 moldy-ui-caption"
                  onClick={handleAddFile}
                  disabled={setFile.isPending || !newPath.trim()}
                >
                  {t('create')}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-6 flex-1 moldy-ui-caption"
                  onClick={() => {
                    setAdding(false)
                    setNewPath('')
                  }}
                >
                  {t('cancel')}
                </Button>
              </div>
            </div>
          ) : null}
          <SkillPackageTree files={files ?? []} selectedPath={selectedPath} onSelect={selectFile} />
          {dirtyPaths.size > 0 ? (
            <p className="mt-3 moldy-ui-micro text-muted-foreground">
              {t('unsavedCount', { count: dirtyPaths.size })}
            </p>
          ) : null}
        </DialogShell.Sidebar>

        <DialogShell.Body className="flex flex-col gap-3 py-4">
          <SkillCredentialBindingsPanel skillId={skillId} />
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
              entity={t('packageEntity')}
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
            {t('deleteSkill')}
          </Button>
        )}
        <span className="moldy-ui-micro text-muted-foreground">
          {t('usedBy', {
            bytes: skill?.size_bytes ?? 0,
            version: skill?.version ?? '—',
            count: skill?.used_by_count ?? 0,
          })}
        </span>
        <Button variant="outline" onClick={onClose}>
          {t('close')}
        </Button>
        <Button onClick={handleSave} disabled={!currentDirty || setFile.isPending}>
          {setFile.isPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Save className="size-4" />
          )}
          {t('saveFile')}
        </Button>
      </DialogShell.Footer>
    </>
  )
}

function SkillCredentialBindingsPanel({ skillId }: { skillId: string }) {
  const t = useTranslations('skill.detailDialog')
  const { data: requirements, isLoading: requirementsLoading } =
    useSkillCredentialRequirements(skillId)
  const { data: bindings, isLoading: bindingsLoading } = useSkillCredentialBindings(skillId)
  const setBinding = useSetSkillCredentialBinding(skillId)
  const deleteBinding = useDeleteSkillCredentialBinding(skillId)

  const bindingByKey = useMemo(() => {
    const map = new Map<string, string>()
    bindings?.forEach((binding) => {
      map.set(binding.requirement_key, binding.credential_id)
    })
    return map
  }, [bindings])

  const loading = requirementsLoading || bindingsLoading
  const pending = setBinding.isPending || deleteBinding.isPending

  async function handleCredentialChange(requirementKey: string, credentialId: string | null) {
    try {
      if (credentialId) {
        await setBinding.mutateAsync({ requirementKey, credentialId })
        toast.success(t('credentialUpdated'))
      } else {
        await deleteBinding.mutateAsync(requirementKey)
        toast.success(t('credentialCleared'))
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('credentialUpdateFailed'))
    }
  }

  if (loading) {
    return <Skeleton className="h-20 w-full rounded-lg" />
  }

  if (!requirements?.length) {
    return null
  }

  return (
    <section className="rounded-lg border border-border/70 bg-muted/20 p-3">
      <div className="mb-3 space-y-0.5">
        <h3 className="text-sm font-semibold text-foreground">{t('credentialBindingsTitle')}</h3>
        <p className="text-xs text-muted-foreground">{t('credentialBindingsDescription')}</p>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {requirements.map((requirement) => {
          const current = bindingByKey.get(requirement.key) ?? null
          return (
            <div key={requirement.key} className="space-y-1.5">
              <div className="flex min-h-5 items-center gap-2">
                <label className="text-xs font-medium text-foreground">
                  {requirement.label || requirement.key}
                </label>
                <Badge variant={requirement.required ? 'default' : 'secondary'} className="h-5">
                  {requirement.required ? t('requiredCredential') : t('optionalCredential')}
                </Badge>
              </div>
              {requirement.description ? (
                <p className="line-clamp-2 moldy-ui-caption leading-4 text-muted-foreground">
                  {requirement.description}
                </p>
              ) : null}
              <CredentialPicker
                value={current}
                onChange={(next) => handleCredentialChange(requirement.key, next)}
                definitionKeys={[requirement.definition_key]}
                disabled={pending}
                placeholder={t('credentialPlaceholder')}
              />
              <p className="font-mono moldy-ui-micro text-muted-foreground/80">
                {requirement.definition_key}
              </p>
            </div>
          )
        })}
      </div>
    </section>
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
  const t = useTranslations('skill.detailDialog')
  if (!selectedPath) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {t('pickFile')}
      </div>
    )
  }

  return (
    <>
      <div className="flex items-center gap-2">
        <code className="rounded bg-muted/50 px-2 py-0.5 font-mono moldy-ui-caption">
          {selectedPath}
        </code>
        {currentDirty ? (
          <Badge variant="secondary" className="bg-status-warn/15 moldy-ui-micro text-status-warn">
            {t('unsaved')}
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
            {t('deleteFile')}
          </Button>
        ) : (
          <Badge variant="secondary" className="ml-auto moldy-ui-micro text-muted-foreground">
            {t('protected')}
          </Badge>
        )}
      </div>

      {confirmingFileDelete ? (
        <DeleteConfirmInline
          entity={t('fileEntity')}
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
  const t = useTranslations('skill.detailDialog')
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
          <Download className="size-3" /> {t('openOriginal')}
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
          <Download className="size-3.5" /> {t('openPdf')}
        </a>
      </div>
    )
  }

  if (!isTextFile(path)) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-md border border-border/60 bg-muted/20 p-6 text-center">
        <p className="text-sm font-medium">{t('binaryFile')}</p>
        <p className="text-xs text-muted-foreground">
          {t('binaryUnavailable')}
        </p>
        <a
          href={skillsApi.fileUrl(skillId, path)}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-background px-3 py-1.5 text-xs hover:bg-muted"
        >
          <Download className="size-3.5" /> {t('download')}
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
