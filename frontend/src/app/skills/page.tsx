'use client'

import { useState } from 'react'
import {
  PlusIcon,
  Loader2Icon,
  Trash2Icon,
  PencilIcon,
  BookOpenIcon,
  SaveIcon,
  XIcon,
} from 'lucide-react'
import { toast } from 'sonner'
import { useTranslations, useFormatter } from 'next-intl'
import { useSkills, useCreateSkill, useUpdateSkill, useDeleteSkill } from '@/lib/hooks/use-skills'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/shared/empty-state'
import { PageHeader } from '@/components/shared/page-header'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogTrigger,
} from '@/components/ui/dialog'

function SkillFormDialog({
  trigger,
  initialData,
  onSubmit,
  isPending,
  title,
}: {
  trigger: React.ReactElement
  initialData?: { name: string; description: string; content: string }
  onSubmit: (data: { name: string; description: string; content: string }) => void
  isPending: boolean
  title: string
}) {
  const t = useTranslations('skill')
  const tc = useTranslations('common')
  const [open, setOpen] = useState(false)
  const [name, setName] = useState(initialData?.name ?? '')
  const [description, setDescription] = useState(initialData?.description ?? '')
  const [content, setContent] = useState(initialData?.content ?? '')

  function handleOpen(isOpen: boolean) {
    setOpen(isOpen)
    if (isOpen && initialData) {
      setName(initialData.name)
      setDescription(initialData.description)
      setContent(initialData.content)
    } else if (isOpen) {
      setName('')
      setDescription('')
      setContent('')
    }
  }

  function handleSubmit() {
    if (!name.trim() || !content.trim()) return
    onSubmit({ name: name.trim(), description: description.trim(), content: content.trim() })
    setOpen(false)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      <DialogTrigger render={trigger} />
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{t('dialogDescription')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 pt-2">
          <div className="space-y-2">
            <label className="text-sm font-medium">{t('form.name')}</label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('form.namePlaceholder')}
            />
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">{t('form.description')}</label>
            <Input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t('form.descriptionPlaceholder')}
            />
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">{t('form.content')}</label>
            <Textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder={t('form.contentPlaceholder')}
              rows={10}
              className="font-mono text-xs"
            />
          </div>

          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              <XIcon className="size-4" data-icon="inline-start" />
              {tc('cancel')}
            </Button>
            <Button onClick={handleSubmit} disabled={isPending || !name.trim() || !content.trim()}>
              {isPending ? (
                <Loader2Icon className="size-4 animate-spin" data-icon="inline-start" />
              ) : (
                <SaveIcon className="size-4" data-icon="inline-start" />
              )}
              {tc('save')}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

export default function SkillsPage() {
  const { data: skills, isLoading } = useSkills()
  const createSkill = useCreateSkill()
  const deleteSkill = useDeleteSkill()
  const t = useTranslations('skill')

  async function handleCreate(data: { name: string; description: string; content: string }) {
    try {
      await createSkill.mutateAsync(data)
      toast.success(t('toast.created'))
    } catch {
      toast.error(t('toast.createFailed'))
    }
  }

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader
        title={t('pageTitle')}
        action={
          <SkillFormDialog
            title={t('dialogTitle.new')}
            trigger={
              <Button>
                <PlusIcon className="size-4" data-icon="inline-start" />
                {t('addSkill')}
              </Button>
            }
            onSubmit={handleCreate}
            isPending={createSkill.isPending}
          />
        }
      />

      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i}>
              <CardHeader>
                <Skeleton className="h-5 w-32" />
                <Skeleton className="h-4 w-full" />
              </CardHeader>
              <CardContent>
                <Skeleton className="h-20 w-full" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : skills && skills.length > 0 ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {skills.map((skill) => (
            <SkillCard
              key={skill.id}
              skill={skill}
              onDelete={() => {
                deleteSkill.mutate(skill.id, {
                  onSuccess: () => toast.success(t('toast.deleted')),
                })
              }}
              isDeleting={deleteSkill.isPending}
            />
          ))}
        </div>
      ) : (
        <EmptyState
          icon={<BookOpenIcon className="size-6" />}
          title={t('empty.title')}
          description={t('empty.description')}
          action={
            <SkillFormDialog
              title={t('dialogTitle.new')}
              trigger={
                <Button>
                  <PlusIcon className="size-4" data-icon="inline-start" />
                  {t('firstSkill')}
                </Button>
              }
              onSubmit={async (data) => {
                try {
                  await createSkill.mutateAsync(data)
                  toast.success(t('toast.created'))
                } catch {
                  toast.error(t('toast.createFailed'))
                }
              }}
              isPending={createSkill.isPending}
            />
          }
        />
      )}
    </div>
  )
}

function SkillCard({
  skill,
  onDelete,
  isDeleting,
}: {
  skill: {
    id: string
    name: string
    description: string | null
    content: string
    updated_at: string
  }
  onDelete: () => void
  isDeleting: boolean
}) {
  const updateSkill = useUpdateSkill(skill.id)
  const t = useTranslations('skill')
  const format = useFormatter()

  return (
    <Card className="flex flex-col">
      <CardHeader>
        <CardTitle className="text-sm">{skill.name}</CardTitle>
        {skill.description && (
          <CardDescription className="line-clamp-2 text-xs">{skill.description}</CardDescription>
        )}
      </CardHeader>
      <CardContent className="flex-1">
        <pre className="rounded bg-muted p-2 text-[11px] text-muted-foreground line-clamp-4 whitespace-pre-wrap font-mono">
          {skill.content}
        </pre>
      </CardContent>
      <CardFooter className="justify-between">
        <span className="text-[10px] text-muted-foreground">
          {t('modified', {
            date: format.dateTime(new Date(skill.updated_at), { dateStyle: 'medium' }),
          })}
        </span>
        <div className="flex gap-1">
          <SkillFormDialog
            title={t('dialogTitle.edit')}
            initialData={{
              name: skill.name,
              description: skill.description ?? '',
              content: skill.content,
            }}
            trigger={
              <Button variant="ghost" size="icon-sm">
                <PencilIcon className="size-3.5" />
              </Button>
            }
            onSubmit={async (data) => {
              try {
                await updateSkill.mutateAsync(data)
                toast.success(t('toast.updated'))
              } catch {
                toast.error(t('toast.updateFailed'))
              }
            }}
            isPending={updateSkill.isPending}
          />
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onDelete}
            disabled={isDeleting}
            className="text-muted-foreground hover:text-destructive"
          >
            {isDeleting ? (
              <Loader2Icon className="size-3.5 animate-spin" />
            ) : (
              <Trash2Icon className="size-3.5" />
            )}
          </Button>
        </div>
      </CardFooter>
    </Card>
  )
}
