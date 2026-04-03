"use client"

import { useState, useRef, useEffect } from "react"
import {
  SparklesIcon,
  SendIcon,
  Loader2Icon,
  BotIcon,
  UserIcon,
  CheckCircle2Icon,
  AlertCircleIcon,
} from "lucide-react"
import { toast } from "sonner"
import { useQueryClient } from "@tanstack/react-query"

import { apiFetch } from "@/lib/api/client"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogTrigger,
} from "@/components/ui/dialog"

interface FixAgentDialogProps {
  agentId: string
  agentName: string
}

interface FixMessage {
  role: "user" | "assistant"
  content: string
  action?: string
  summary?: string
  changes?: Record<string, unknown>
}

interface FixResponse {
  content: string
  action: string
  changes?: Record<string, unknown>
  summary?: string
  question?: string
  conversation_history: Array<{ role: string; content: string }>
}

export function FixAgentDialog({ agentId, agentName }: FixAgentDialogProps) {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<FixMessage[]>([])
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [history, setHistory] = useState<Array<{ role: string; content: string }>>([])
  const scrollRef = useRef<HTMLDivElement>(null)
  const qc = useQueryClient()

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages])

  function handleOpen(isOpen: boolean) {
    setOpen(isOpen)
    if (!isOpen) {
      setMessages([])
      setInput("")
      setHistory([])
    }
  }

  async function handleSend() {
    const text = input.trim()
    if (!text || isLoading) return

    setInput("")
    setMessages((prev) => [...prev, { role: "user", content: text }])
    setIsLoading(true)

    try {
      const resp = await apiFetch<FixResponse>(`/api/agents/${agentId}/fix`, {
        method: "POST",
        body: JSON.stringify({
          content: text,
          conversation_history: history,
        }),
      })

      setHistory(resp.conversation_history)
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: resp.content,
          action: resp.action,
          summary: resp.summary,
          changes: resp.changes as Record<string, unknown> | undefined,
        },
      ])

      if (resp.action === "apply") {
        toast.success(resp.summary ?? "변경사항이 적용되었습니다")
        qc.invalidateQueries({ queryKey: ["agents"] })
        qc.invalidateQueries({ queryKey: ["agents", agentId] })
      }
    } catch {
      toast.error("Fix Agent 호출에 실패했습니다")
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "오류가 발생했습니다. 다시 시도해주세요." },
      ])
    } finally {
      setIsLoading(false)
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      <DialogTrigger
        render={
          <Button variant="outline" size="sm" className="gap-1.5">
            <SparklesIcon className="size-4" />
            AI로 수정하기
          </Button>
        }
      />
      <DialogContent className="sm:max-w-xl h-[600px] flex flex-col p-0">
        <DialogHeader className="px-6 pt-6 pb-2">
          <DialogTitle className="flex items-center gap-2">
            <SparklesIcon className="size-5 text-primary" />
            Fix Agent
          </DialogTitle>
          <DialogDescription>
            대화로 &quot;{agentName}&quot; 에이전트를 수정합니다.
            프롬프트, 도구, 모델을 자유롭게 변경해보세요.
          </DialogDescription>
        </DialogHeader>

        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-auto px-6 space-y-4">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-center text-muted-foreground">
              <SparklesIcon className="size-8 mb-3 text-primary/40" />
              <p className="text-sm font-medium">어떻게 수정할까요?</p>
              <div className="mt-3 flex flex-wrap justify-center gap-2">
                {[
                  "존댓말로 바꿔줘",
                  "더 간결하게 답변하도록",
                  "비용을 줄여줘",
                  "검색 도구를 추가해줘",
                ].map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    className="rounded-full border px-3 py-1 text-xs hover:bg-accent transition-colors"
                    onClick={() => {
                      setInput(suggestion)
                    }}
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`flex gap-2.5 ${msg.role === "user" ? "justify-end" : ""}`}>
              {msg.role === "assistant" && (
                <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
                  <BotIcon className="size-3.5 text-primary" />
                </div>
              )}
              <div className={`max-w-[85%] space-y-2 ${msg.role === "user" ? "text-right" : ""}`}>
                <div
                  className={`rounded-2xl px-3.5 py-2 text-sm leading-relaxed ${
                    msg.role === "user"
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted"
                  }`}
                >
                  {msg.content}
                </div>

                {/* Action badge */}
                {msg.action === "preview" && (
                  <div className="flex items-center gap-1.5">
                    <Badge variant="outline" className="text-xs gap-1">
                      <AlertCircleIcon className="size-3" />
                      미리보기
                    </Badge>
                    {msg.summary && (
                      <span className="text-xs text-muted-foreground">{msg.summary}</span>
                    )}
                  </div>
                )}
                {msg.action === "apply" && (
                  <div className="flex items-center gap-1.5">
                    <Badge className="text-xs gap-1 bg-emerald-500/10 text-emerald-600 hover:bg-emerald-500/10">
                      <CheckCircle2Icon className="size-3" />
                      적용 완료
                    </Badge>
                    {msg.summary && (
                      <span className="text-xs text-muted-foreground">{msg.summary}</span>
                    )}
                  </div>
                )}
              </div>
              {msg.role === "user" && (
                <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted">
                  <UserIcon className="size-3.5" />
                </div>
              )}
            </div>
          ))}

          {isLoading && (
            <div className="flex gap-2.5">
              <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
                <BotIcon className="size-3.5 text-primary" />
              </div>
              <div className="rounded-2xl bg-muted px-3.5 py-2.5">
                <Loader2Icon className="size-4 animate-spin text-muted-foreground" />
              </div>
            </div>
          )}
        </div>

        {/* Input */}
        <div className="border-t px-4 py-3">
          <div className="flex items-end gap-2">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="수정 요청을 입력하세요..."
              rows={1}
              className="min-h-[40px] max-h-[120px] resize-none text-sm"
            />
            <Button
              size="icon"
              onClick={handleSend}
              disabled={!input.trim() || isLoading}
              className="shrink-0"
            >
              {isLoading ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <SendIcon className="size-4" />
              )}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
