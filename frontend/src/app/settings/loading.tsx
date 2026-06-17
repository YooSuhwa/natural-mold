import { Skeleton } from '@/components/ui/skeleton'

export default function Loading() {
  return (
    <div className="flex flex-1 flex-col overflow-auto p-6">
      <main className="mx-auto w-full min-w-0 max-w-5xl">
        <div className="space-y-4">
          <div className="space-y-2">
            <Skeleton className="h-7 w-52" />
            <Skeleton className="h-5 w-full max-w-[560px]" />
          </div>
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
            <div className="space-y-4">
              <Skeleton className="h-[260px] w-full" />
              <Skeleton className="h-[180px] w-full" />
            </div>
            <Skeleton className="h-[220px] w-full" />
          </div>
        </div>
      </main>
    </div>
  )
}
