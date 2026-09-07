import type { ConversationRunInput } from '@/lib/api/conversation-run-inputs'
import type { QueueOperationState, QueueRunStartAcceptance } from './server-message-queue-contract'

type TimedClaim = {
  readonly input: ConversationRunInput
  readonly claimedAt: number
}

function timedClaim(input: ConversationRunInput): TimedClaim | null {
  if (input.claimed_at === null) return null
  const claimedAt = Date.parse(input.claimed_at)
  return Number.isFinite(claimedAt) ? { input, claimedAt } : null
}

export function createClaimedRunTracker(onClaimedRun: (runId: string) => void) {
  const followedRunIds = new Set<string>()
  let newestObservedClaimedAt: number | null = null

  const recordAccepted = (
    accepted: QueueRunStartAcceptance,
    requestId: string,
  ): QueueOperationState => {
    if (!accepted.runId) {
      return { kind: 'queued', requestId, inputId: accepted.inputId }
    }
    if (!followedRunIds.has(accepted.runId)) {
      followedRunIds.add(accepted.runId)
      onClaimedRun(accepted.runId)
    }
    return {
      kind: 'applied',
      requestId,
      inputId: accepted.inputId,
      runId: accepted.runId,
    }
  }

  const observeInputs = (
    inputs: readonly ConversationRunInput[],
    operation: QueueOperationState,
  ): QueueOperationState => {
    const claimedInputs = inputs.filter((input) => input.status === 'claimed' && input.run_id)
    const observedClaimTimes = claimedInputs
      .filter((input) => input.run_id && followedRunIds.has(input.run_id))
      .map(timedClaim)
      .filter((claim): claim is TimedClaim => claim !== null)
    if (observedClaimTimes.length > 0) {
      newestObservedClaimedAt = Math.max(
        newestObservedClaimedAt ?? Number.NEGATIVE_INFINITY,
        ...observedClaimTimes.map((claim) => claim.claimedAt),
      )
    }
    const unseenClaims = claimedInputs.filter(
      (input) => input.run_id && !followedRunIds.has(input.run_id),
    )
    const operationClaim = unseenClaims.find((input) => {
      if ('inputId' in operation && input.id === operation.inputId) return true
      return (
        (operation.kind === 'sending' ||
          operation.kind === 'reconciling' ||
          operation.kind === 'queued') &&
        input.client_request_id === operation.requestId
      )
    })
    let claimToFollow = operationClaim
    if (!claimToFollow && unseenClaims.length > 0) {
      const timedClaims = unseenClaims.map(timedClaim)
      if (timedClaims.every((claim): claim is TimedClaim => claim !== null)) {
        const newestClaimedAt = Math.max(...timedClaims.map((claim) => claim.claimedAt))
        const newest = timedClaims.filter((claim) => claim.claimedAt === newestClaimedAt)
        if (
          newest.length === 1 &&
          (newestObservedClaimedAt === null || newestClaimedAt > newestObservedClaimedAt)
        ) {
          claimToFollow = newest[0]?.input
        } else if (newestClaimedAt <= (newestObservedClaimedAt ?? Number.NEGATIVE_INFINITY)) {
          for (const claim of timedClaims) {
            if (claim.input.run_id) followedRunIds.add(claim.input.run_id)
          }
        }
      }
    }
    // A complete queue listing contains historical claimed rows. Follow the exact
    // accepted operation when known; otherwise only a uniquely newest claim is safe.
    if (!claimToFollow?.run_id) return operation

    const selectedTimedClaim = timedClaim(claimToFollow)
    followedRunIds.add(claimToFollow.run_id)
    if (selectedTimedClaim) {
      newestObservedClaimedAt = Math.max(
        newestObservedClaimedAt ?? Number.NEGATIVE_INFINITY,
        selectedTimedClaim.claimedAt,
      )
      for (const input of unseenClaims) {
        const candidate = timedClaim(input)
        if (candidate && candidate.claimedAt < selectedTimedClaim.claimedAt && input.run_id) {
          followedRunIds.add(input.run_id)
        }
      }
    }
    onClaimedRun(claimToFollow.run_id)
    if ('inputId' in operation && operation.inputId === claimToFollow.id) {
      return {
        kind: 'applied',
        requestId: operation.requestId,
        inputId: claimToFollow.id,
        runId: claimToFollow.run_id,
      }
    }
    return operation
  }

  return { observeInputs, recordAccepted }
}
