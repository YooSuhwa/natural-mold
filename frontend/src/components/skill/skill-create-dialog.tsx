'use client'

import { useRef, useState } from 'react'
import { toast } from 'sonner'
import JSZip from 'jszip'
import { FileText, Loader2, Sparkles, Upload } from 'lucide-react'

import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { DialogShell } from '@/components/shared/dialog-shell'
import { FormFooter } from '@/components/shared/form-footer'
import {
  useCreateTextSkill,
  useUploadPackageSkill,
} from '@/lib/hooks/use-skills'

type TabKey = 'text' | 'package' | 'scratch'

interface SkillCreateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  initialTab?: TabKey
  /**
   * Called after successful creation. Receives the new skill id so the caller
   * can immediately open the detail dialog (especially useful for Package /
   * From-scratch flows that produce multi-file skills).
   */
  onCreated?: (skillId: string) => void
}

export function SkillCreateDialog({
  open,
  onOpenChange,
  initialTab = 'text',
  onCreated,
}: SkillCreateDialogProps) {
  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="lg" height="fixed">
      {open ? (
        <SkillCreateBody
          key={initialTab}
          initialTab={initialTab}
          onClose={() => onOpenChange(false)}
          onCreated={onCreated}
        />
      ) : null}
    </DialogShell>
  )
}

function SkillCreateBody({
  initialTab,
  onClose,
  onCreated,
}: {
  initialTab: TabKey
  onClose: () => void
  onCreated?: (skillId: string) => void
}) {
  const [tab, setTab] = useState<TabKey>(initialTab)

  return (
    <>
      <DialogShell.Header
        title="New skill"
        description="Create a text snippet, upload a package, or scaffold a folder you can edit inline."
      />
      <DialogShell.Body>
        <Tabs value={tab} onValueChange={(v) => setTab(v as TabKey)}>
          <TabsList variant="line">
            <TabsTrigger value="text">
              <FileText className="size-3.5" /> Text
            </TabsTrigger>
            <TabsTrigger value="package">
              <Upload className="size-3.5" /> Package
            </TabsTrigger>
            <TabsTrigger value="scratch">
              <Sparkles className="size-3.5" /> From scratch
            </TabsTrigger>
          </TabsList>

          <TabsContent value="text" className="pt-4">
            <TextTab onClose={onClose} onCreated={onCreated} />
          </TabsContent>
          <TabsContent value="package" className="pt-4">
            <PackageTab onClose={onClose} onCreated={onCreated} />
          </TabsContent>
          <TabsContent value="scratch" className="pt-4">
            <ScratchTab onClose={onClose} onCreated={onCreated} />
          </TabsContent>
        </Tabs>
      </DialogShell.Body>
    </>
  )
}

// ──────────────────────────────────────────────────────────────────────────
// Tab 1 — Text skill (single markdown body)
// ──────────────────────────────────────────────────────────────────────────

function TextTab({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated?: (skillId: string) => void
}) {
  const create = useCreateTextSkill()
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [description, setDescription] = useState('')
  const [content, setContent] = useState('')

  const canSubmit = name.trim() && content.trim()

  async function handleSubmit() {
    if (!canSubmit) {
      toast.error('Name and content are required')
      return
    }
    try {
      const created = await create.mutateAsync({
        name: name.trim(),
        slug: slug.trim() || undefined,
        description: description.trim() || null,
        content,
      })
      toast.success('Skill created')
      onCreated?.(created.id)
      onClose()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed')
    }
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <label htmlFor="skill-name">
            Name <span className="text-destructive">*</span>
          </label>
          <Input
            id="skill-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="skill-slug">Slug</label>
          <Input
            id="skill-slug"
            value={slug}
            placeholder="(auto-generated)"
            onChange={(e) => setSlug(e.target.value)}
          />
        </div>
      </div>
      <div className="space-y-1.5">
        <label htmlFor="skill-desc">Description</label>
        <Input
          id="skill-desc"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <label htmlFor="skill-content">
          Content (markdown) <span className="text-destructive">*</span>
        </label>
        <Textarea
          id="skill-content"
          value={content}
          rows={10}
          className="font-mono text-xs"
          onChange={(e) => setContent(e.target.value)}
        />
      </div>
      <div className="mt-auto flex justify-end gap-2 pt-2">
        <FormFooter
          onCancel={onClose}
          onSubmit={handleSubmit}
          pending={create.isPending}
          disabled={!canSubmit}
        />
      </div>
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────
// Tab 2 — Package upload (.zip / .skill)
// ──────────────────────────────────────────────────────────────────────────

function PackageTab({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated?: (skillId: string) => void
}) {
  const upload = useUploadPackageSkill()
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)

  async function handleUpload() {
    if (!file) return
    try {
      const created = await upload.mutateAsync(file)
      toast.success('Skill package uploaded')
      onCreated?.(created.id)
      onClose()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Upload failed')
    }
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        Upload a ZIP archive containing a SKILL.md with YAML frontmatter, plus
        any auxiliary files (markdown, code, images). Max 10MB.
      </p>
      <div className="space-y-3">
        <input
          ref={inputRef}
          type="file"
          accept=".skill,.zip"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-primary-strong/15 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-primary-strong hover:file:bg-primary-strong/25"
        />
        {file ? (
          <p className="rounded-md border border-border/60 bg-muted/40 px-2 py-1.5 text-xs">
            <span className="font-medium">{file.name}</span> ·{' '}
            {(file.size / 1024).toFixed(1)} kb
          </p>
        ) : null}
      </div>
      <div className="mt-auto flex justify-end gap-2 pt-2">
        <FormFooter
          onCancel={onClose}
          onSubmit={handleUpload}
          submitLabel={
            <>
              <Upload className="mr-1 size-4" /> Upload
            </>
          }
          pending={upload.isPending}
          disabled={!file}
        />
      </div>
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────
// Tab 3 — From scratch (build minimal SKILL.md zip in-browser)
// ──────────────────────────────────────────────────────────────────────────

function ScratchTab({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated?: (skillId: string) => void
}) {
  const upload = useUploadPackageSkill()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)

  const canSubmit = name.trim().length > 0 && !busy

  async function handleSubmit() {
    if (!canSubmit) return
    setBusy(true)
    try {
      const slug = slugify(name)
      const skillMd = buildSkillMd(name.trim(), description.trim(), slug)
      const zip = new JSZip()
      const folder = zip.folder(slug)
      if (!folder) throw new Error('Failed to scaffold zip')
      folder.file('SKILL.md', skillMd)
      const blob = await zip.generateAsync({ type: 'blob' })
      const file = new File([blob], `${slug}.skill`, {
        type: 'application/zip',
      })
      const created = await upload.mutateAsync(file)
      toast.success('Skill scaffolded — opening editor…')
      onCreated?.(created.id)
      onClose()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Scaffold failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        Create an empty package skill with just a SKILL.md stub. The detail
        editor opens automatically so you can add files inline.
      </p>
      <div className="space-y-1.5">
        <label htmlFor="scratch-name">
          Name <span className="text-destructive">*</span>
        </label>
        <Input
          id="scratch-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="my-helpful-skill"
        />
      </div>
      <div className="space-y-1.5">
        <label htmlFor="scratch-desc">Description</label>
        <Textarea
          id="scratch-desc"
          value={description}
          rows={3}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="One sentence describing what this skill does."
        />
      </div>
      <div className="mt-auto flex justify-end gap-2 pt-2">
        <FormFooter
          onCancel={onClose}
          onSubmit={handleSubmit}
          submitLabel={
            <>
              {busy ? (
                <Loader2 className="mr-1 size-4 animate-spin" />
              ) : (
                <Sparkles className="mr-1 size-4" />
              )}
              Scaffold &amp; edit
            </>
          }
          pending={busy || upload.isPending}
          disabled={!canSubmit}
        />
      </div>
    </div>
  )
}

function slugify(value: string): string {
  return (
    value
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 64) || 'untitled-skill'
  )
}

function buildSkillMd(
  name: string,
  description: string,
  slug: string,
): string {
  const desc = description || `Skill: ${name}`
  // Escape any double quotes in description for YAML safety.
  const yamlDesc = desc.replace(/"/g, '\\"')
  return `---
name: ${slug}
description: "${yamlDesc}"
version: "0.1.0"
---

# ${name}

${desc}

<!--
Add instructions, examples, and any auxiliary files (scripts, references)
to this folder. The agent will read SKILL.md first, then load other files
on demand.
-->
`
}
