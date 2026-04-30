'use client'

import { useRef, useState } from 'react'
import { toast } from 'sonner'
import { Loader2, Upload } from 'lucide-react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { useUploadPackageSkill } from '@/lib/hooks/use-skills'

interface SkillUploadDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function SkillUploadDialog({ open, onOpenChange }: SkillUploadDialogProps) {
  const upload = useUploadPackageSkill()
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)

  function handleClose(next: boolean) {
    if (!next) {
      setFile(null)
      if (inputRef.current) inputRef.current.value = ''
    }
    onOpenChange(next)
  }

  async function handleUpload() {
    if (!file) return
    try {
      await upload.mutateAsync(file)
      toast.success('Skill package uploaded')
      handleClose(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Upload failed')
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Upload skill package</DialogTitle>
          <DialogDescription>
            ZIP file containing a SKILL.md frontmatter (.skill).
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <input
            ref={inputRef}
            type="file"
            accept=".skill,.zip"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full text-sm"
          />
          {file && (
            <p className="rounded border bg-muted/40 px-2 py-1 text-xs">
              {file.name} · {(file.size / 1024).toFixed(1)}kb
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => handleClose(false)}>
            Cancel
          </Button>
          <Button onClick={handleUpload} disabled={!file || upload.isPending}>
            {upload.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Upload className="size-4" />
            )}
            Upload
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
