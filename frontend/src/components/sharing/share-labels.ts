import type { ShareSurface } from './share-backend'

/** i18n key (under `sharing`) for a share or access level; agents "run" what others "use". */
export function levelLabelKey(surface: ShareSurface, level: string): string {
  if (level === 'consumer' && surface === 'agent') return 'level.consumerAgent'
  return `level.${level}`
}
