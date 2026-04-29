'use client'

import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Loader2, Save, Trash2 } from 'lucide-react'

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Separator } from '@/components/ui/separator'
import { Badge } from '@/components/ui/badge'
import { SkillPackageTree } from './skill-package-tree'
import {
  useDeleteSkill,
  useSkill,
  useSkillContent,
  useSkillFiles,
  useUpdateSkillContent,
} from '@/lib/hooks/use-skills'
import { skillsApi } from '@/lib/api/skills'

interface SkillDetailSheetProps {
  skillId: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function SkillDetailSheet({ skillId, open, onOpenChange }: SkillDetailSheetProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      {skillId ? (
        // Key on `skillId` so each new selection gets fresh local state and
        // useState initializers can read the cached query result directly.
        <SkillDetailBody key={skillId} skillId={skillId} onClose={() => onOpenChange(false)} />
      ) : (
        <SheetContent className="w-full sm:max-w-xl flex flex-col gap-4 overflow-y-auto p-0">
          <SheetHeader className="border-b">
            <SheetTitle>Loading...</SheetTitle>
          </SheetHeader>
        </SheetContent>
      )}
    </Sheet>
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

  // Initial editor state seeded from the (possibly cached) text content.
  // Component is keyed by skill id, so this initializer runs once per skill.
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

  return (
    <SheetContent className="w-full sm:max-w-xl flex flex-col gap-4 overflow-y-auto p-0">
      <SheetHeader className="border-b">
        {skill ? (
          <>
            <div className="flex items-center gap-2">
              <SheetTitle>{skill.name}</SheetTitle>
              <Badge variant="secondary" className="text-[10px]">
                {skill.kind}
              </Badge>
            </div>
            <SheetDescription>{skill.description ?? skill.slug}</SheetDescription>
          </>
        ) : (
          <SheetTitle>Loading...</SheetTitle>
        )}
      </SheetHeader>

      {skill && (
        <div className="flex-1 px-4 pb-4 space-y-3">
          {isText && (
            <>
              <Textarea
                value={editor}
                rows={18}
                className="font-mono text-xs"
                onChange={(e) => setEditor(e.target.value)}
              />
              <div className="flex justify-end gap-2">
                <Button size="sm" onClick={handleSave} disabled={update.isPending}>
                  {update.isPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Save className="size-4" />
                  )}
                  Save
                </Button>
              </div>
            </>
          )}

          {isPackage && (
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
                <pre className="max-h-80 overflow-auto rounded border bg-muted/30 p-2 font-mono text-[11px] whitespace-pre-wrap">
                  {previewBody || 'Pick a file to preview'}
                </pre>
              </div>
            </div>
          )}

          <Separator />

          <div className="space-y-1 text-xs text-muted-foreground">
            <p>Used by {skill.used_by_count} agent(s)</p>
            <p>Size {skill.size_bytes}b</p>
            <p>Version {skill.version ?? '—'}</p>
            <p>Updated {new Date(skill.updated_at).toLocaleString()}</p>
          </div>

          <Button
            variant="outline"
            size="sm"
            className="text-destructive hover:text-destructive"
            onClick={() => setConfirming(true)}
          >
            <Trash2 className="size-3.5" />
            Delete
          </Button>

          {confirming && (
            <div className="rounded border border-destructive/40 bg-destructive/5 p-3 text-xs">
              <p className="font-medium text-destructive">Delete this skill?</p>
              <div className="mt-2 flex gap-2">
                <Button size="sm" variant="outline" onClick={() => setConfirming(false)}>
                  Cancel
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={handleDelete}
                  disabled={remove.isPending}
                >
                  Confirm delete
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </SheetContent>
  )
}
