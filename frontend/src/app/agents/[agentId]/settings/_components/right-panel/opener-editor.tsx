'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { CheckIcon, PencilIcon, PlusIcon, Trash2Icon, XIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { DialogShell } from '@/components/shared/dialog-shell'

interface OpenerEditorProps {
  questions: string[]
  onChange: (questions: string[]) => void
  max?: number
}

export function OpenerEditor({ questions, onChange, max = 12 }: OpenerEditorProps) {
  const t = useTranslations('agent.settings')
  const tc = useTranslations('common')

  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  const [editValue, setEditValue] = useState('')
  const [addOpen, setAddOpen] = useState(false)
  const [addValue, setAddValue] = useState('')

  const isFull = questions.length >= max

  function startEdit(index: number) {
    setEditingIndex(index)
    setEditValue(questions[index])
  }

  function cancelEdit() {
    setEditingIndex(null)
    setEditValue('')
  }

  function saveEdit() {
    if (editingIndex === null) return
    const next = editValue.trim().slice(0, 200)
    if (!next) {
      cancelEdit()
      return
    }
    onChange(questions.map((q, i) => (i === editingIndex ? next : q)))
    cancelEdit()
  }

  function handleRemove(index: number) {
    if (editingIndex === index) cancelEdit()
    onChange(questions.filter((_, i) => i !== index))
  }

  function openAdd() {
    setAddValue('')
    setAddOpen(true)
  }

  function confirmAdd() {
    const next = addValue.trim().slice(0, 200)
    if (!next || isFull) return
    onChange([...questions, next])
    setAddOpen(false)
    setAddValue('')
  }

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-0.5">
          <h3 className="text-sm font-semibold">{t('openerTitle')}</h3>
          <p className="text-xs text-muted-foreground">{t('openerDescription')}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="text-xs tabular-nums text-muted-foreground">
            {t('openerCounter', { count: questions.length, max })}
          </span>
          <Button size="sm" variant="outline" onClick={openAdd} disabled={isFull}>
            <PlusIcon className="size-3.5" />
            {t('openerAdd')}
          </Button>
        </div>
      </div>

      {questions.length === 0 ? (
        <div className="flex items-center justify-center rounded-lg border border-dashed py-8 text-sm text-muted-foreground">
          {t('openerEmpty')}
        </div>
      ) : (
        <ul className="space-y-2">
          {questions.map((q, index) => {
            const isEditing = editingIndex === index
            return (
              <li
                key={index}
                className="group flex items-center gap-3 rounded-lg border px-3 py-2.5 transition-colors hover:bg-muted/30"
              >
                <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-xs tabular-nums text-muted-foreground">
                  {index + 1}
                </span>
                {isEditing ? (
                  <>
                    <Input
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      maxLength={200}
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') saveEdit()
                        if (e.key === 'Escape') cancelEdit()
                      }}
                      className="h-8 flex-1 shadow-none focus-visible:border-input focus-visible:ring-0 focus-visible:ring-offset-0"
                    />
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={saveEdit}
                      aria-label={t('openerSave')}
                    >
                      <CheckIcon className="moldy-status-success moldy-status-icon size-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={cancelEdit}
                      aria-label={t('openerCancelEdit')}
                    >
                      <XIcon className="size-4 text-muted-foreground" />
                    </Button>
                  </>
                ) : (
                  <>
                    <span className="flex-1 truncate text-sm">{q}</span>
                    <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => startEdit(index)}
                        aria-label={t('openerEdit')}
                      >
                        <PencilIcon className="size-3.5 text-muted-foreground" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => handleRemove(index)}
                        aria-label={t('openerDelete')}
                      >
                        <Trash2Icon className="size-3.5 text-muted-foreground" />
                      </Button>
                    </div>
                  </>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {isFull && (
        <p className="text-xs text-muted-foreground">{t('openerMaxReached', { max })}</p>
      )}

      <DialogShell open={addOpen} onOpenChange={setAddOpen} size="sm" height="auto">
        <DialogShell.Header title={t('openerAddDialogTitle')} />
        <DialogShell.Body>
          <Input
            value={addValue}
            onChange={(e) => setAddValue(e.target.value)}
            placeholder={t('openerPlaceholder')}
            maxLength={200}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter') confirmAdd()
            }}
            className="shadow-none focus-visible:border-input focus-visible:ring-0 focus-visible:ring-offset-0"
          />
        </DialogShell.Body>
        <DialogShell.Footer>
          <Button variant="ghost" onClick={() => setAddOpen(false)}>
            {tc('cancel')}
          </Button>
          <Button onClick={confirmAdd} disabled={!addValue.trim()}>
            {t('openerAdd')}
          </Button>
        </DialogShell.Footer>
      </DialogShell>
    </div>
  )
}
