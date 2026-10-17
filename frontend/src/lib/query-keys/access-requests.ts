import type { RequestBox } from '@/lib/types/access-request'

/** `['access-requests', box, cursor]`; dropped on an organization switch (not retained). */
export const accessRequestQueryKeys = {
  all: ['access-requests'] as const,
  box: (box: RequestBox, cursor: string | null) => ['access-requests', box, cursor] as const,
}
