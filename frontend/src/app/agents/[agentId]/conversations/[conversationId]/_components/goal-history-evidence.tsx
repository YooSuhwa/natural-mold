import { useId, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { HistoryEvidencePanel } from '@/components/chat/compaction-summary'
import { useGoalEvidenceHistoryReference } from '@/lib/hooks/use-conversation-goal'
import type { CompactionEvidenceTarget } from '@/lib/hooks/use-conversation-history-evidence'
import type { GoalHistoryReceipt } from '@/lib/types/conversation-goal'

function GoalHistoryDisclosure({ target }: { target: CompactionEvidenceTarget }) {
  const [expanded, setExpanded] = useState(false)
  const panelId = useId()
  const t = useTranslations('chat.goal')

  return (
    <div className="mt-1 text-xs">
      <Button
        type="button"
        variant="ghost"
        size="xs"
        aria-expanded={expanded}
        aria-controls={panelId}
        onClick={() => setExpanded(!expanded)}
      >
        {t(expanded ? 'hideHistoryEvidence' : 'showHistoryEvidence')}
      </Button>
      <div id={panelId} hidden={!expanded} className="moldy-muted-panel p-3">
        {expanded && <HistoryEvidencePanel target={target} />}
      </div>
    </div>
  )
}

export function GoalHistoryEvidence(receipt: GoalHistoryReceipt) {
  const query = useGoalEvidenceHistoryReference(receipt)
  const ref = query.data
  if (
    query.isError ||
    !ref ||
    ref.source_run_id !== receipt.producingRunId ||
    typeof ref.checkpoint_id !== 'string' ||
    !/^[A-Za-z0-9_-]{1,64}$/.test(ref.checkpoint_id) ||
    typeof ref.history_id !== 'string' ||
    !/^history_[0-9a-f]{24}$/.test(ref.history_id) ||
    !Number.isSafeInteger(ref.cutoff_index) ||
    ref.cutoff_index < 0
  )
    return null

  return (
    <GoalHistoryDisclosure
      key={JSON.stringify([ref.checkpoint_id, ref.history_id, ref.cutoff_index])}
      target={{
        conversationId: receipt.conversationId,
        historyId: ref.history_id,
        checkpointId: ref.checkpoint_id,
        cutoffIndex: ref.cutoff_index,
      }}
    />
  )
}
