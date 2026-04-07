'use client'

import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface ComingSoonButtonProps {
  children: React.ReactNode
  title?: string
  className?: string
  message?: string
}

export function ComingSoonButton({ children, title, className, message }: ComingSoonButtonProps) {
  const tc = useTranslations('common')
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      onClick={() => toast.info(message ?? tc('comingSoon.default'))}
      className={cn(
        'opacity-50 hover:opacity-70 cursor-pointer transition-opacity duration-200',
        className,
      )}
      title={title}
    >
      {children}
    </Button>
  )
}
