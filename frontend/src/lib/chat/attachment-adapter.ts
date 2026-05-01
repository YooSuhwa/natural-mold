'use client'

import type {
  AttachmentAdapter,
  PendingAttachment,
  CompleteAttachment,
} from '@assistant-ui/react'
import { uploadFile } from '@/lib/api/uploads'

/**
 * Bridge our REST upload endpoint to the assistant-ui ``AttachmentAdapter``
 * contract. The adapter:
 * - accepts text, common image types, and PDFs (mirrors the backend allow-list);
 * - performs the network upload during ``send`` so abort during composition
 *   doesn't waste a backend write;
 * - tags ``content`` with a markdown link so model providers that flatten
 *   attachments to text still see something useful.
 *
 * The pending attachment id starts as a local UUID; once ``send`` resolves we
 * replace it with the server upload id (``UploadResponse.id``) — that's what
 * the conversations POST body needs to link the row to the message.
 */
export class MoldyAttachmentAdapter implements AttachmentAdapter {
  accept = 'image/*,text/*,application/pdf,application/json'

  async add(state: { file: File }): Promise<PendingAttachment> {
    const id = `local-${crypto.randomUUID()}`
    const isImage = state.file.type.startsWith('image/')
    return {
      id,
      type: isImage ? 'image' : 'file',
      name: state.file.name,
      contentType: state.file.type,
      file: state.file,
      status: { type: 'requires-action', reason: 'composer-send' },
    }
  }

  async send(attachment: PendingAttachment): Promise<CompleteAttachment> {
    const uploaded = await uploadFile(attachment.file)
    const isImage = uploaded.mime_type.startsWith('image/')
    return {
      id: uploaded.id,
      type: isImage ? 'image' : 'file',
      name: uploaded.filename,
      contentType: uploaded.mime_type,
      file: attachment.file,
      status: { type: 'complete' },
      content: [
        {
          type: 'text',
          text: `[attachment: ${uploaded.filename}](${uploaded.url})`,
        },
      ],
    }
  }

  async remove(): Promise<void> {
    // No backend cleanup yet — orphan upload rows are reaped by a future job.
    // The frontend just drops the entry from the composer state.
  }
}

export const moldyAttachmentAdapter = new MoldyAttachmentAdapter()
