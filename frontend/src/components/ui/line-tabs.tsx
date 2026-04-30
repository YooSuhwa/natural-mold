'use client'

import { Tabs as TabsPrimitive } from '@base-ui/react/tabs'

import { cn } from '@/lib/utils'
import { TabsList, TabsTrigger } from '@/components/ui/tabs'

/**
 * Underline-style tab list with the project's accent (emerald) styling.
 *
 * Same building blocks as `TabsList variant="line"` + the emerald active
 * styling we already use in the agent workbench right panel + manual create
 * page's form/visual switcher. Centralized here so the same look can be
 * shared by other surfaces (e.g. tools/skills dialog right pane).
 */
function LineTabsList({ className, ...props }: TabsPrimitive.List.Props) {
  return <TabsList variant="line" className={cn('h-auto', className)} {...props} />
}

function LineTabsTrigger({ className, ...props }: TabsPrimitive.Tab.Props) {
  return (
    <TabsTrigger
      className={cn(
        'px-4 py-2.5 after:bg-emerald-500 data-active:text-emerald-600 dark:after:bg-emerald-400 dark:data-active:text-emerald-400',
        className,
      )}
      {...props}
    />
  )
}

export { LineTabsList, LineTabsTrigger }
