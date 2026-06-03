'use client'

import { Tabs as TabsPrimitive } from '@base-ui/react/tabs'

import { cn } from '@/lib/utils'
import { TabsList, TabsTrigger } from '@/components/ui/tabs'

/**
 * Underline-style tab list with the project's Moldy accent styling.
 *
 * Same building blocks as `TabsList variant="line"` + the primary active
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
        'px-4 py-2.5 after:bg-primary-strong data-active:text-primary-strong',
        className,
      )}
      {...props}
    />
  )
}

export { LineTabsList, LineTabsTrigger }
