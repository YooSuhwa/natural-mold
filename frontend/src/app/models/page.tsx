"use client"

import { useState } from "react"
import {
  PlusIcon,
  CpuIcon,
  Trash2Icon,
  Loader2Icon,
  StarIcon,
} from "lucide-react"
import { useModels, useCreateModel, useDeleteModel } from "@/lib/hooks/use-models"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogTrigger,
} from "@/components/ui/dialog"
import { EmptyState } from "@/components/shared/empty-state"
import { PageHeader } from "@/components/shared/page-header"

const providers = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "google", label: "Google" },
  { value: "custom", label: "기타" },
]

export default function ModelsPage() {
  const { data: models, isLoading } = useModels()
  const createModel = useCreateModel()
  const deleteModel = useDeleteModel()

  const [open, setOpen] = useState(false)
  const [provider, setProvider] = useState("openai")
  const [modelName, setModelName] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [baseUrl, setBaseUrl] = useState("")
  const [apiKey, setApiKey] = useState("")

  function resetForm() {
    setProvider("openai")
    setModelName("")
    setDisplayName("")
    setBaseUrl("")
    setApiKey("")
  }

  async function handleCreate() {
    await createModel.mutateAsync({
      provider,
      model_name: modelName,
      display_name: displayName || modelName,
      base_url: baseUrl || undefined,
      api_key: apiKey || undefined,
    })
    resetForm()
    setOpen(false)
  }

  function getProviderIcon(p: string) {
    switch (p) {
      case "openai":
        return "OAI"
      case "anthropic":
        return "ANT"
      case "google":
        return "GGL"
      default:
        return "AI"
    }
  }

  return (
    <div className="flex flex-1 flex-col gap-6 p-6">
      <PageHeader
        title="모델 관리"
        action={
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger
              render={
                <Button>
                  <PlusIcon className="size-4" data-icon="inline-start" />
                  모델 추가
                </Button>
              }
            />
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>모델 추가</DialogTitle>
                <DialogDescription>
                  새 LLM 모델을 등록합니다.
                </DialogDescription>
              </DialogHeader>

              <div className="space-y-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">제공자</label>
                  <Select value={provider} onValueChange={(val) => { if (val) setProvider(val) }}>
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="제공자 선택" />
                    </SelectTrigger>
                    <SelectContent>
                      {providers.map((p) => (
                        <SelectItem key={p.value} value={p.value}>
                          {p.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium">
                    모델명 <span className="text-destructive">*</span>
                  </label>
                  <Input
                    value={modelName}
                    onChange={(e) => setModelName(e.target.value)}
                    placeholder="gpt-4o"
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium">표시 이름</label>
                  <Input
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder="GPT-4o"
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium">Base URL</label>
                  <Input
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    placeholder="https://api.openai.com/v1"
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium">API Key</label>
                  <Input
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    type="password"
                    placeholder="sk-xxxxxxxxxxxx"
                  />
                </div>
              </div>

              <DialogFooter>
                <Button
                  onClick={handleCreate}
                  disabled={!modelName.trim() || createModel.isPending}
                >
                  {createModel.isPending && (
                    <Loader2Icon className="mr-1 size-4 animate-spin" />
                  )}
                  등록
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : models && models.length > 0 ? (
        <div className="space-y-2">
          {models.map((model) => (
            <Card key={model.id}>
              <CardContent className="flex items-center justify-between py-3">
                <div className="flex items-center gap-3">
                  <div className="flex size-9 items-center justify-center rounded-lg bg-muted text-xs font-bold text-muted-foreground">
                    {getProviderIcon(model.provider)}
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">
                        {model.display_name}
                      </span>
                      <Badge variant="outline">{model.provider}</Badge>
                      {model.is_default && (
                        <Badge variant="secondary">
                          <StarIcon className="mr-0.5 size-3" />
                          기본
                        </Badge>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {model.model_name}
                    </p>
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`${model.display_name} 삭제`}
                  onClick={() => deleteModel.mutate(model.id)}
                  disabled={deleteModel.isPending}
                >
                  {deleteModel.isPending ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : (
                    <Trash2Icon className="size-4 text-muted-foreground" />
                  )}
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={<CpuIcon className="size-6" />}
          title="등록된 모델이 없습니다."
          description="모델을 추가하여 에이전트에서 사용하세요."
        />
      )}
    </div>
  )
}
