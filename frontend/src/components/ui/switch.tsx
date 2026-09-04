'use client'

import { Switch as SwitchPrimitive } from '@base-ui/react/switch'

import { cn } from '@/lib/utils'

function Switch({ className, ...props }: SwitchPrimitive.Root.Props) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        'group/switch inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-transparent bg-input p-0.5 transition-colors outline-hidden focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 data-checked:bg-primary dark:bg-input/80',
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className="pointer-events-none size-4 translate-x-0 rounded-full bg-background transition-transform data-checked:translate-x-4 dark:bg-foreground"
      />
    </SwitchPrimitive.Root>
  )
}

export { Switch }
