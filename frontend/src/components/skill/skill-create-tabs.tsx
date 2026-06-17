'use client'

import { useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Sparkles, Upload } from 'lucide-react'

import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { FormFooter } from '@/components/shared/form-footer'
import { useCreateTextSkill, useUploadPackageSkill } from '@/lib/hooks/use-skills'

export function SkillCreateChatTab({
  onCancel,
  onStart,
}: {
  readonly onCancel: () => void
  readonly onStart: (request: string) => void
}) {
  const t = useTranslations('skill.createDialog.chat')
  const [request, setRequest] = useState('')
  const canSubmit = request.trim().length > 0

  function handleSubmit() {
    if (!canSubmit) {
      toast.error(t('required'))
      return
    }
    onStart(request.trim())
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <p className="text-sm text-muted-foreground">{t('description')}</p>
      <div className="space-y-1.5">
        <label htmlFor="skill-chat-request">
          {t('request')} <span className="text-destructive">*</span>
        </label>
        <Textarea
          id="skill-chat-request"
          value={request}
          rows={8}
          onChange={(event) => setRequest(event.target.value)}
          placeholder={t('placeholder')}
        />
      </div>
      <div className="mt-auto flex justify-end gap-2 pt-2">
        <FormFooter
          onCancel={onCancel}
          onSubmit={handleSubmit}
          submitLabel={
            <>
              <Sparkles className="mr-1 size-4" /> {t('submit')}
            </>
          }
          disabled={!canSubmit}
        />
      </div>
    </div>
  )
}

export function SkillCreateTextTab({
  onClose,
  onCreated,
}: {
  readonly onClose: () => void
  readonly onCreated?: (skillId: string) => void
}) {
  const t = useTranslations('skill.createDialog.text')
  const create = useCreateTextSkill()
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [description, setDescription] = useState('')
  const [content, setContent] = useState('')

  const canSubmit = Boolean(name.trim() && content.trim())

  async function handleSubmit() {
    if (!canSubmit) {
      toast.error(t('required'))
      return
    }
    try {
      const created = await create.mutateAsync({
        name: name.trim(),
        slug: slug.trim() || undefined,
        description: description.trim() || null,
        content,
      })
      toast.success(t('created'))
      onCreated?.(created.id)
      onClose()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('saveFailed'))
    }
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <label htmlFor="skill-name">
            {t('name')} <span className="text-destructive">*</span>
          </label>
          <Input id="skill-name" value={name} onChange={(event) => setName(event.target.value)} />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="skill-slug">{t('slug')}</label>
          <Input
            id="skill-slug"
            value={slug}
            placeholder={t('slugPlaceholder')}
            onChange={(event) => setSlug(event.target.value)}
          />
        </div>
      </div>
      <div className="space-y-1.5">
        <label htmlFor="skill-desc">{t('description')}</label>
        <Input
          id="skill-desc"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <label htmlFor="skill-content">
          {t('content')} <span className="text-destructive">*</span>
        </label>
        <Textarea
          id="skill-content"
          value={content}
          rows={10}
          className="font-mono text-xs"
          onChange={(event) => setContent(event.target.value)}
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

export function SkillCreatePackageTab({
  onClose,
  onCreated,
}: {
  readonly onClose: () => void
  readonly onCreated?: (skillId: string) => void
}) {
  const t = useTranslations('skill.createDialog.package')
  const upload = useUploadPackageSkill()
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)

  async function handleUpload() {
    if (!file) return
    try {
      const created = await upload.mutateAsync(file)
      toast.success(t('uploaded'))
      onCreated?.(created.id)
      onClose()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('uploadFailed'))
    }
  }

  return (
    <div className="flex h-full flex-col gap-4">
      <p className="text-sm text-muted-foreground">{t('description')}</p>
      <div className="space-y-3">
        <input
          ref={inputRef}
          type="file"
          accept=".skill,.zip"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          className="block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-primary-strong/15 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-primary-strong hover:file:bg-primary-strong/25"
        />
        {file ? (
          <p className="rounded-md border border-border/60 bg-muted/40 px-2 py-1.5 text-xs">
            <span className="font-medium">{file.name}</span> · {(file.size / 1024).toFixed(1)} kb
          </p>
        ) : null}
      </div>
      <div className="mt-auto flex justify-end gap-2 pt-2">
        <FormFooter
          onCancel={onClose}
          onSubmit={handleUpload}
          submitLabel={
            <>
              <Upload className="mr-1 size-4" /> {t('submit')}
            </>
          }
          pending={upload.isPending}
          disabled={!file}
        />
      </div>
    </div>
  )
}
