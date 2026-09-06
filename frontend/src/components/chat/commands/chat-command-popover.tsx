'use client'

import { ComposerPrimitive, unstable_useSlashCommandAdapter } from '@assistant-ui/react'

import triggerStyles from '@/components/chat/context/chat-composer-triggers.module.css'
import type { ChatCommandDefinition } from '@/lib/chat/commands/chat-command-types'
import { cn } from '@/lib/utils'

export function ChatCommandPopover({
  commands,
  ariaLabel,
}: {
  readonly commands: readonly ChatCommandDefinition[]
  readonly ariaLabel: string
}) {
  const slash = unstable_useSlashCommandAdapter({
    commands: commands.map((command) => ({
      id: command.id,
      label: command.label,
      description:
        command.availability.kind === 'disabled'
          ? command.availability.reason
          : command.description,
      execute: () => {
        if (command.availability.kind === 'enabled') void command.execute('')
      },
    })),
    removeOnExecute: true,
  })

  return (
    <ComposerPrimitive.Unstable_TriggerPopover
      char="/"
      adapter={slash.adapter}
      aria-label={ariaLabel}
      className={cn(
        'moldy-popover mb-2 max-h-72 overflow-y-auto p-1',
        triggerStyles.popoverPosition,
      )}
    >
      <ComposerPrimitive.Unstable_TriggerPopover.Action {...slash.action} />
      <ComposerPrimitive.Unstable_TriggerPopoverItems>
        {(items) =>
          items.map((item) => {
            const definition = commands.find((candidate) => candidate.id === item.id)
            const disabled = definition?.availability.kind === 'disabled'
            return (
              <ComposerPrimitive.Unstable_TriggerPopoverItem
                key={item.id}
                item={item}
                disabled={disabled}
                className="flex w-full items-start gap-2 px-2.5 py-2 text-left text-sm transition-colors hover:bg-accent data-highlighted:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
              >
                <span className="font-medium text-foreground">{item.label}</span>
                {item.description ? (
                  <span className="min-w-0 text-muted-foreground">{item.description}</span>
                ) : null}
              </ComposerPrimitive.Unstable_TriggerPopoverItem>
            )
          })
        }
      </ComposerPrimitive.Unstable_TriggerPopoverItems>
    </ComposerPrimitive.Unstable_TriggerPopover>
  )
}
