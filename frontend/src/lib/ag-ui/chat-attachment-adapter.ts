import type { AttachmentAdapter, CompleteAttachment } from '@assistant-ui/react'

export function agUiAttachmentAdapter(original: AttachmentAdapter): AttachmentAdapter {
  // The server can accept the upload before a queued message is acknowledged.
  // Retrying the same selected attachment must reuse its confirmed upload ID.
  const uploads = new WeakMap<File, Map<string, Promise<CompleteAttachment>>>()

  return {
    accept: original.accept,
    add: (state) => original.add(state),
    remove: (attachment) => {
      if (attachment.file) uploads.get(attachment.file)?.delete(attachment.id)
      return original.remove(attachment)
    },
    send: (attachment, options) => {
      const selected =
        uploads.get(attachment.file) ?? new Map<string, Promise<CompleteAttachment>>()
      const cached = selected.get(attachment.id)
      if (cached) return cached
      const upload = original.send(attachment, options).then(
        (completed): CompleteAttachment => ({
          ...completed,
          content: [
            {
              type: 'file',
              sourceType: 'id',
              data: completed.id,
              mimeType: completed.contentType || 'application/octet-stream',
              filename: completed.name,
            },
          ],
        }),
        (error: unknown) => {
          selected.delete(attachment.id)
          throw error
        },
      )
      selected.set(attachment.id, upload)
      uploads.set(attachment.file, selected)
      return upload
    },
  }
}
