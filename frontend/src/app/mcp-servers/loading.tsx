import { ResourceGrid } from '@/components/shared/resource-layout'
import { Skeleton } from '@/components/ui/skeleton'

export default function Loading() {
  return (
    <div className="moldy-app-surface relative flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="mx-auto flex min-h-0 w-full max-w-7xl flex-1 flex-col gap-5 px-5 py-6 md:px-8">
        <Skeleton className="h-16 w-full" />
        <div className="moldy-resource-panel">
          <div className="moldy-resource-panel-toolbar md:p-5">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full max-w-sm" />
          </div>
          <div className="moldy-resource-panel-body bg-background/30 md:p-5">
            <ResourceGrid minColumnWidth={300}>
              {Array.from({ length: 6 }).map((_, index) => (
                <Skeleton key={index} className="moldy-skeleton-card h-48 w-full" />
              ))}
            </ResourceGrid>
          </div>
        </div>
      </div>
    </div>
  )
}
