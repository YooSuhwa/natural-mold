'use client'

import { useCallback, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { skillsApi } from '@/lib/api/skills'
import {
  useDeleteSkill,
  useDeleteSkillFile,
  useSetSkillFile,
  useSkill,
  useSkillFiles,
} from '@/lib/hooks/use-skills'

import { FileEditorPane } from './skill-file-editor-pane'
import { SkillCredentialBindingsPanel } from './skill-credential-bindings-panel'
import { isTextFile } from './skill-detail-file-utils'
import { SkillDetailPackageFooter } from './skill-detail-package-footer'
import { SkillDetailPackageSidebar } from './skill-detail-package-sidebar'
import type { SkillDetailTabRender } from './skill-detail-tab-shell'
import { useSkillFileRemoteCache } from './use-skill-file-remote-cache'

export function PackageSkillEditor({
  children,
  skillId,
  onClose,
  showCredentials = true,
}: {
  readonly children: SkillDetailTabRender
  readonly skillId: string
  readonly onClose: () => void
  readonly showCredentials?: boolean
}) {
  const t = useTranslations('skill.detailDialog')
  const { data: skill } = useSkill(skillId)
  const { data: files } = useSkillFiles(skillId)
  const setFile = useSetSkillFile(skillId)
  const deleteFile = useDeleteSkillFile(skillId)
  const removeSkill = useDeleteSkill()
  const fileEntries = useMemo(() => (files ?? []).filter((file) => !file.is_dir), [files])
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Map<string, string>>(new Map())
  const [confirmingSkillDelete, setConfirmingSkillDelete] = useState(false)
  const [confirmingFileDelete, setConfirmingFileDelete] = useState(false)
  const [adding, setAdding] = useState(false)
  const [newPath, setNewPath] = useState('')
  const handleLoadError = useCallback(
    (message: string) => toast.error(t('loadFailed', { message })),
    [t],
  )
  const { remoteCache, setRemoteCache } = useSkillFileRemoteCache({
    skillId,
    cacheKey: skill?.content_hash,
    selectedPath,
    onLoadError: handleLoadError,
  })

  if (selectedPath === null && fileEntries.length > 0) {
    const skillMd = fileEntries.find((file) => file.path.endsWith('SKILL.md'))
    setSelectedPath(skillMd?.path ?? fileEntries[0].path)
  }

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
      const ok = window.confirm(t('discardConfirm', { path: selectedPath ?? '' }))
      if (!ok) return
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
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('saveFailed'))
    }
  }

  async function handleDeleteFile() {
    if (!selectedPath) return
    try {
      await deleteFile.mutateAsync(selectedPath)
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
      setSelectedPath(null)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('deleteFailed'))
    }
  }

  async function handleAddFile() {
    const trimmed = newPath.trim().replace(/^\/+/, '')
    if (!trimmed) {
      toast.error(t('pathRequired'))
      return
    }
    if (fileEntries.some((file) => file.path === trimmed)) {
      toast.error(t('fileExists'))
      return
    }
    try {
      await setFile.mutateAsync({ path: trimmed, content: '' })
      toast.success(t('fileCreated', { path: trimmed }))
      setNewPath('')
      setAdding(false)
      setRemoteCache((prev) => {
        const next = new Map(prev)
        next.set(trimmed, '')
        return next
      })
      setSelectedPath(trimmed)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('createFailed'))
    }
  }

  async function handleDeleteSkill() {
    try {
      await removeSkill.mutateAsync(skillId)
      toast.success(t('deleted'))
      onClose()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('deleteFailed'))
    }
  }

  return children({
    sidebar: (
      <SkillDetailPackageSidebar
        files={files ?? []}
        selectedPath={selectedPath}
        dirtyCount={dirtyPaths.size}
        adding={adding}
        newPath={newPath}
        addPending={setFile.isPending}
        onSelect={selectFile}
        onToggleAdding={() => setAdding((value) => !value)}
        onNewPathChange={setNewPath}
        onAddFile={handleAddFile}
        onCancelAdd={() => {
          setAdding(false)
          setNewPath('')
        }}
      />
    ),
    sidebarClassName: 'w-64',
    body: (
      <>
        {showCredentials ? <SkillCredentialBindingsPanel skillId={skillId} /> : null}
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
      </>
    ),
    bodyClassName: 'flex flex-col gap-3 py-4',
    footer: (
      <SkillDetailPackageFooter
        confirmingDelete={confirmingSkillDelete}
        deletePending={removeSkill.isPending}
        savePending={setFile.isPending}
        saveDisabled={!currentDirty || setFile.isPending}
        exportHref={skillsApi.exportUrl(skillId)}
        sizeBytes={skill?.size_bytes ?? 0}
        version={skill?.version ?? null}
        usedByCount={skill?.used_by_count ?? 0}
        onAskDelete={() => setConfirmingSkillDelete(true)}
        onCancelDelete={() => setConfirmingSkillDelete(false)}
        onConfirmDelete={handleDeleteSkill}
        onClose={onClose}
        onSave={handleSave}
      />
    ),
  })
}
