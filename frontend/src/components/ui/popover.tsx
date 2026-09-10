'use client'

import { Popover as Primitive } from '@base-ui/react/popover'
import { cn } from '@/lib/utils'

export const Popover = Primitive.Root
export const PopoverTrigger = Primitive.Trigger
export const PopoverTitle = Primitive.Title
export const PopoverDescription = Primitive.Description

export function PopoverContent({
  anchor,
  side = 'top',
  align = 'start',
  className,
  children,
  ...props
}: Primitive.Popup.Props & Pick<Primitive.Positioner.Props, 'anchor' | 'side' | 'align'>) {
  return (
    <Primitive.Portal>
      <Primitive.Positioner
        anchor={anchor}
        side={side}
        align={align}
        sideOffset={8}
        collisionPadding={12}
        positionMethod="fixed"
        className="isolate z-50"
      >
        <Primitive.Popup
          {...props}
          className={cn(
            'moldy-popover max-w-[calc(100vw-1.5rem)] bg-popover text-popover-foreground',
            'transition-opacity duration-150 motion-reduce:transition-none data-starting-style:opacity-0 data-ending-style:opacity-0',
            className,
          )}
        >
          {children}
        </Primitive.Popup>
      </Primitive.Positioner>
    </Primitive.Portal>
  )
}
