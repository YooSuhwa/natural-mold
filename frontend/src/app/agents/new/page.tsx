"use client"

import Link from "next/link"
import { MessageSquareIcon, LayoutTemplateIcon } from "lucide-react"
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { PageHeader } from "@/components/shared/page-header"

export default function AgentNewPage() {
  return (
    <div className="flex flex-1 flex-col gap-8 p-6">
      <PageHeader title="새 에이전트 만들기" />

      <div className="mx-auto grid w-full max-w-3xl gap-6 sm:grid-cols-2">
        <Card className="transition-all hover:ring-2 hover:ring-primary/20 hover:shadow-md">
          <CardHeader className="text-center">
            <div className="mx-auto mb-2 flex size-12 items-center justify-center rounded-full bg-primary/10 text-primary">
              <MessageSquareIcon className="size-6" />
            </div>
            <CardTitle>대화로 만들기</CardTitle>
            <CardDescription>
              AI와 대화하며 에이전트를 구성합니다
            </CardDescription>
          </CardHeader>
          <CardContent className="flex justify-center">
            <Button render={<Link href="/agents/new/conversational" />}>
              시작하기
            </Button>
          </CardContent>
        </Card>

        <Card className="transition-all hover:ring-2 hover:ring-primary/20 hover:shadow-md">
          <CardHeader className="text-center">
            <div className="mx-auto mb-2 flex size-12 items-center justify-center rounded-full bg-primary/10 text-primary">
              <LayoutTemplateIcon className="size-6" />
            </div>
            <CardTitle>템플릿으로 만들기</CardTitle>
            <CardDescription>
              준비된 템플릿에서 골라 바로 시작합니다
            </CardDescription>
          </CardHeader>
          <CardContent className="flex justify-center">
            <Button
              variant="outline"
              render={<Link href="/agents/new/template" />}
            >
              둘러보기
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
