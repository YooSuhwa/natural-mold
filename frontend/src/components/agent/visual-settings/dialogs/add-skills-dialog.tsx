'use client'

import { useState, useMemo } from 'react'
import { SearchIcon } from 'lucide-react'
import Link from 'next/link'
import { useTranslations } from 'next-intl'

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { Skill } from '@/lib/types'

interface AddSkillsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  allSkills: Skill[]
  selectedSkillIds: Set<string>
  onToggleSkill: (skillId: string) => void
}

export function AddSkillsDialog({
  open,
  onOpenChange,
  allSkills,
  selectedSkillIds,
  onToggleSkill,
}: AddSkillsDialogProps) {
  const t = useTranslations('agent.visualSettings.addSkillsDialog')
  const [search, setSearch] = useState('')
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set())
  const [previewId, setPreviewId] = useState<string | null>(null)

  const filteredSkills = useMemo(() => {
    if (!search.trim()) return allSkills
    const q = search.toLowerCase()
    return allSkills.filter(
      (skill) =>
        skill.name.toLowerCase().includes(q) || skill.description?.toLowerCase().includes(q),
    )
  }, [allSkills, search])

  const previewSkill = allSkills.find((skill) => skill.id === previewId) ?? null

  function handleToggleCheck(skillId: string) {
    setCheckedIds((prev) => {
      const next = new Set(prev)
      if (next.has(skillId)) next.delete(skillId)
      else next.add(skillId)
      return next
    })
  }

  function handleAdd() {
    for (const id of checkedIds) {
      if (!selectedSkillIds.has(id)) {
        onToggleSkill(id)
      }
    }
    handleClose()
  }

  function handleClose() {
    setSearch('')
    setCheckedIds(new Set())
    setPreviewId(null)
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent showCloseButton className="w-[900px] max-w-[900px] gap-0 p-0 sm:max-w-[900px]">
        <DialogHeader className="px-4 pt-4 pb-3">
          <DialogTitle>{t('title')}</DialogTitle>
          <DialogDescription>{t('description')}</DialogDescription>
        </DialogHeader>

        <div className="flex border-t border-border" style={{ height: '460px' }}>
          {/* Left panel */}
          <div className="flex w-1/3 flex-col border-r border-border">
            {/* Search */}
            <div className="relative px-3 py-2">
              <SearchIcon className="absolute top-1/2 left-5 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder={t('searchPlaceholder')}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="h-7 pl-7 text-xs"
              />
            </div>

            {/* Skill list */}
            <div className="flex-1 overflow-y-auto">
              {filteredSkills.length === 0 ? (
                <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                  {t('noResults')}
                </div>
              ) : (
                filteredSkills.map((skill) => {
                  const isAlreadyAdded = selectedSkillIds.has(skill.id)
                  const isChecked = checkedIds.has(skill.id)
                  return (
                    <div
                      key={skill.id}
                      className={`flex cursor-pointer items-start gap-2 px-3 py-2 hover:bg-muted/50 ${
                        previewId === skill.id ? 'bg-muted' : ''
                      }`}
                      onClick={() => setPreviewId(skill.id)}
                    >
                      <input
                        type="checkbox"
                        checked={isChecked || isAlreadyAdded}
                        disabled={isAlreadyAdded}
                        onChange={() => handleToggleCheck(skill.id)}
                        onClick={(e) => e.stopPropagation()}
                        className="mt-0.5 size-3.5 shrink-0 rounded border-input"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-xs font-medium">{skill.name}</div>
                        {skill.description && (
                          <div className="line-clamp-2 text-[10px] text-muted-foreground">
                            {skill.description}
                          </div>
                        )}
                      </div>
                    </div>
                  )
                })
              )}
            </div>

            {/* Footer link */}
            <div className="border-t border-border px-3 py-2">
              <Button variant="ghost" size="xs" render={<Link href="/skills" />}>
                {t('manageSkills')}
              </Button>
            </div>
          </div>

          {/* Right panel */}
          <div className="flex w-2/3 flex-col">
            {previewSkill ? (
              <div className="flex flex-1 flex-col overflow-y-auto p-4">
                <h3 className="text-sm font-medium">{previewSkill.name}</h3>
                {previewSkill.description && (
                  <p className="mt-2 text-xs text-muted-foreground">{previewSkill.description}</p>
                )}
                {previewSkill.content && (
                  <div className="mt-3">
                    <span className="text-[10px] font-medium uppercase text-muted-foreground">
                      {t('content')}
                    </span>
                    <pre className="mt-1 max-h-[280px] overflow-auto rounded-md bg-muted p-2 text-[10px] whitespace-pre-wrap">
                      {previewSkill.content}
                    </pre>
                  </div>
                )}
              </div>
            ) : (
              <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
                {t('selectToPreview')}
              </div>
            )}

            {/* Add button */}
            <div className="flex justify-end border-t border-border px-4 py-3">
              <Button size="sm" disabled={checkedIds.size === 0} onClick={handleAdd}>
                {checkedIds.size > 0 ? t('addCount', { count: checkedIds.size }) : t('add')}
              </Button>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
