'use client'

import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Loader2, Save, Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { DialogShell } from '@/components/shared/dialog-shell'
import { DeleteConfirmInline } from '@/components/shared/delete-confirm-inline'
import { SkillPackageTree } from './skill-package-tree'
import {
  useDeleteSkill,
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
    <DialogShell open={open} onOpenChange={onOpenChange} size="xl">
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

  const { data: textContent } = useSkillContent(skillId, isText)
  const { data: files } = useSkillFiles(isPackage ? skillId : null)
  const update = useUpdateSkillContent()
  const remove = useDeleteSkill()

  const [editor, setEditor] = useState(() => textContent?.content ?? '')
  const [confirming, setConfirming] = useState(false)
  const [previewPath, setPreviewPath] = useState<string | null>(null)
  const [previewBody, setPreviewBody] = useState<string>('')

  useEffect(() => {
    if (!previewPath) return
    let cancelled = false
    const url = skillsApi.fileUrl(skillId, previewPath)
    fetch(url)
      .then((r) => r.text())
      .then((body) => {
        if (!cancelled) setPreviewBody(body)
      })
      .catch(() => {
        if (!cancelled) setPreviewBody('(failed to load file)')
      })
    return () => {
      cancelled = true
    }
  }, [skillId, previewPath])

  async function handleSave() {
    if (!skill) return
    try {
      await update.mutateAsync({ id: skill.id, data: { content: editor } })
      toast.success('Saved')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed')
    }
  }

  async function handleDelete() {
    if (!skill) return
    try {
      await remove.mutateAsync(skill.id)
      toast.success('Skill deleted')
      onClose()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Delete failed')
    }
  }

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

  return (
    <>
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
      <DialogShell.Body>
        {isText ? (
          <div className="space-y-2">
            <Textarea
              value={editor}
              rows={18}
              className="font-mono text-xs"
              onChange={(e) => setEditor(e.target.value)}
            />
            <div className="flex justify-end">
              <Button size="sm" onClick={handleSave} disabled={update.isPending}>
                {update.isPending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Save className="size-4" />
                )}
                Save
              </Button>
            </div>
          </div>
        ) : null}

        {isPackage ? (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Files
              </h3>
              <SkillPackageTree
                files={files ?? []}
                selectedPath={previewPath}
                onSelect={setPreviewPath}
              />
            </div>
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Preview
              </h3>
              <pre className="max-h-80 overflow-auto rounded-md border border-border/60 bg-muted/30 p-2 font-mono text-[11px] whitespace-pre-wrap">
                {previewBody || 'Pick a file to preview'}
              </pre>
            </div>
          </div>
        ) : null}

        <div className="space-y-1 text-xs text-muted-foreground">
          <p>Used by {skill.used_by_count} agent(s)</p>
          <p>Size {skill.size_bytes}b</p>
          <p>Version {skill.version ?? '—'}</p>
          <p>Updated {new Date(skill.updated_at).toLocaleString()}</p>
        </div>
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
            Delete
          </Button>
        )}
        <Button variant="outline" onClick={onClose}>
          Close
        </Button>
      </DialogShell.Footer>
    </>
  )
}
