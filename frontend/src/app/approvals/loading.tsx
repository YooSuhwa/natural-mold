import { Skeleton } from '@/components/ui/skeleton'

export default function Loading() {
  return (
    <div className="moldy-app-surface flex min-h-0 flex-1 flex-col">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-4 px-6 py-7 md:px-8">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-10 w-full max-w-sm" />
        <Skeleton className="h-40 w-full" />
      </div>
    </div>
  )
}
