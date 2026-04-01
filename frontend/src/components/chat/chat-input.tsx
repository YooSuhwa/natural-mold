"use client"

import { useState } from "react"
import { SendIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

interface ChatInputProps {
  onSend: (content: string) => void
  disabled?: boolean
}

export function ChatInput({ onSend, disabled }: ChatInputProps) {
  const [input, setInput] = useState("")

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!input.trim() || disabled) return
    onSend(input.trim())
    setInput("")
  }

  return (
    <form onSubmit={handleSubmit} className="flex gap-2">
      <Input
        value={input}
        onChange={(e) => setInput(e.target.value)}
        placeholder="메시지 입력..."
        disabled={disabled}
        className="flex-1"
      />
      <Button type="submit" disabled={disabled || !input.trim()}>
        <SendIcon className="size-4" />
        <span className="sr-only">전송</span>
      </Button>
    </form>
  )
}
