"use client"

import { useState, useRef, useEffect, useCallback } from "react"
import { useRouter } from "next/navigation"
import { SendIcon, BotIcon, UserIcon, Loader2Icon } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { creationSessionApi } from "@/lib/api/creation-session"
import type { DraftConfig } from "@/lib/types"

interface ChatMessage {
  role: string
  content: string
}

export default function ConversationalCreationPage() {
  const router = useRouter()
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [draftConfig, setDraftConfig] = useState<DraftConfig | null>(null)
  const [isConfirming, setIsConfirming] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  // Start session on mount
  useEffect(() => {
    let cancelled = false
    async function startSession() {
      try {
        setIsLoading(true)
        const session = await creationSessionApi.start()
        if (cancelled) return
        setSessionId(session.id)
        if (session.conversation_history.length > 0) {
          setMessages(session.conversation_history)
        } else {
          setMessages([
            {
              role: "assistant",
              content:
                "어떤 에이전트를 만들고 싶으세요? 원하는 기능이나 용도를 설명해주세요.",
            },
          ])
        }
      } catch {
        setMessages([
          {
            role: "assistant",
            content:
              "세션을 시작하는 데 문제가 발생했습니다. 페이지를 새로고침해주세요.",
          },
        ])
      } finally {
        setIsLoading(false)
      }
    }
    startSession()
    return () => {
      cancelled = true
    }
  }, [])

  async function handleSend() {
    if (!input.trim() || !sessionId || isLoading) return

    const userMessage = input.trim()
    setInput("")
    setMessages((prev) => [...prev, { role: "user", content: userMessage }])
    setIsLoading(true)

    try {
      const response = await creationSessionApi.sendMessage(
        sessionId,
        userMessage
      )
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: response.content },
      ])
      if (response.draft_config) {
        setDraftConfig(response.draft_config as DraftConfig)
      }
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: "오류가 발생했습니다. 다시 시도해주세요.",
        },
      ])
    } finally {
      setIsLoading(false)
    }
  }

  async function handleConfirm() {
    if (!sessionId || isConfirming) return
    setIsConfirming(true)
    try {
      const agent = await creationSessionApi.confirm(sessionId)
      router.push(`/agents/${agent.id}`)
    } catch {
      setIsConfirming(false)
    }
  }

  return (
    <div className="flex flex-1 flex-col">
      <div className="border-b px-6 py-3">
        <h1 className="text-lg font-semibold">에이전트 만들기 (대화)</h1>
      </div>

      {/* Chat messages */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-2xl space-y-4">
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex gap-3 ${msg.role === "user" ? "justify-end" : ""}`}
            >
              {msg.role !== "user" && (
                <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                  <BotIcon className="size-4" />
                </div>
              )}
              <div
                className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                  msg.role === "user"
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted"
                }`}
              >
                {msg.content}
              </div>
              {msg.role === "user" && (
                <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
                  <UserIcon className="size-4" />
                </div>
              )}
            </div>
          ))}

          {isLoading && (
            <div className="flex gap-3">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                <BotIcon className="size-4" />
              </div>
              <div className="rounded-2xl bg-muted px-4 py-3">
                <Loader2Icon className="size-4 animate-spin text-muted-foreground" />
              </div>
            </div>
          )}

          {/* Draft config preview */}
          {draftConfig && draftConfig.is_ready && (
            <Card className="mx-auto max-w-md">
              <CardHeader>
                <CardTitle className="text-base">
                  에이전트를 구성했습니다!
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="space-y-2 text-sm">
                  <div>
                    <span className="text-muted-foreground">이름: </span>
                    <span className="font-medium">
                      {draftConfig.name ?? "-"}
                    </span>
                  </div>
                  {draftConfig.recommended_model && (
                    <div>
                      <span className="text-muted-foreground">모델: </span>
                      <span>{draftConfig.recommended_model}</span>
                    </div>
                  )}
                  {draftConfig.recommended_tool_names &&
                    draftConfig.recommended_tool_names.length > 0 && (
                      <div>
                        <span className="text-muted-foreground">도구: </span>
                        <span>
                          {draftConfig.recommended_tool_names.join(", ")}
                        </span>
                      </div>
                    )}
                  {draftConfig.system_prompt && (
                    <details className="mt-2">
                      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                        프롬프트 미리보기
                      </summary>
                      <pre className="mt-2 max-h-40 overflow-auto rounded-lg bg-muted p-3 text-xs whitespace-pre-wrap">
                        {draftConfig.system_prompt}
                      </pre>
                    </details>
                  )}
                </div>
                <div className="flex gap-2 pt-2">
                  <Button
                    onClick={handleConfirm}
                    disabled={isConfirming}
                    className="flex-1"
                  >
                    {isConfirming ? (
                      <Loader2Icon className="mr-1 size-4 animate-spin" />
                    ) : null}
                    에이전트 생성
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input */}
      <div className="border-t p-4">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            handleSend()
          }}
          className="mx-auto flex max-w-2xl gap-2"
        >
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="메시지 입력..."
            disabled={isLoading || !sessionId}
          />
          <Button
            type="submit"
            disabled={isLoading || !input.trim() || !sessionId}
          >
            <SendIcon className="size-4" />
          </Button>
        </form>
      </div>
    </div>
  )
}
