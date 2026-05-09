import { toast } from 'sonner'

/**
 * Fired by the `QueryProvider` when `apiFetch` reports a session expiry that
 * cannot be recovered via /refresh. Kept as a plain function (not a React
 * component) so it can be invoked from the API client layer indirectly.
 */
export function showSessionExpiredToast(messages: { title: string; description: string }) {
  toast.error(messages.title, {
    description: messages.description,
    duration: 6000,
  })
}
