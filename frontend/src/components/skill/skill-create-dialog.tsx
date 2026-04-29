'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { Loader2 } from 'lucide-react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useCreateTextSkill } from '@/lib/hooks/use-skills'

interface SkillCreateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function SkillCreateDialog({ open, onOpenChange }: SkillCreateDialogProps) {
  const create = useCreateTextSkill()
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [description, setDescription] = useState('')
  const [content, setContent] = useState('')

  function reset() {
    setName('')
    setSlug('')
    setDescription('')
    setContent('')
  }

  function handleClose(next: boolean) {
    if (!next) reset()
    onOpenChange(next)
  }

  async function handleSubmit() {
    if (!name.trim() || !content.trim()) {
      toast.error('Name and content are required')
      return
    }
    try {
      await create.mutateAsync({
        name: name.trim(),
        slug: slug.trim() || undefined,
        description: description.trim() || null,
        content,
      })
      toast.success('Skill created')
      handleClose(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed')
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>New text skill</DialogTitle>
          <DialogDescription>
            Skills are markdown snippets attached to agents to inject specialized knowledge.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-xs font-medium" htmlFor="skill-name">
                Name <span className="text-destructive">*</span>
              </label>
              <Input
                id="skill-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium" htmlFor="skill-slug">
                Slug
              </label>
              <Input
                id="skill-slug"
                value={slug}
                placeholder="(auto-generated)"
                onChange={(e) => setSlug(e.target.value)}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium" htmlFor="skill-desc">
              Description
            </label>
            <Input
              id="skill-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium" htmlFor="skill-content">
              Content (markdown) <span className="text-destructive">*</span>
            </label>
            <Textarea
              id="skill-content"
              value={content}
              rows={12}
              className="font-mono text-xs"
              onChange={(e) => setContent(e.target.value)}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => handleClose(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={create.isPending || !name.trim() || !content.trim()}
          >
            {create.isPending && <Loader2 className="size-4 animate-spin" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
