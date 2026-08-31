import JSZip from 'jszip'
import { describe, expect, it } from 'vitest'

import { scanArtifactContent, scanTraceZip } from './e2e-artifact-secret-scan.mjs'

function scanText(value, secrets = []) {
  return () => scanArtifactContent(Buffer.from(value), secrets)
}

function storedZip({
  localName = 'trace.txt',
  centralName = localName,
  localExtra = '',
  centralExtra = localExtra,
  entryComment = '',
  archiveComment = '',
  payload = 'safe trace',
} = {}) {
  const localNameBytes = Buffer.from(localName)
  const centralNameBytes = Buffer.from(centralName)
  const localExtraBytes = Buffer.from(localExtra)
  const centralExtraBytes = Buffer.from(centralExtra)
  const entryCommentBytes = Buffer.from(entryComment)
  const archiveCommentBytes = Buffer.from(archiveComment)
  const payloadBytes = Buffer.from(payload)
  const local = Buffer.alloc(30)
  local.writeUInt32LE(0x04034b50, 0)
  local.writeUInt16LE(20, 4)
  local.writeUInt32LE(payloadBytes.length, 18)
  local.writeUInt32LE(payloadBytes.length, 22)
  local.writeUInt16LE(localNameBytes.length, 26)
  local.writeUInt16LE(localExtraBytes.length, 28)
  const localRecord = Buffer.concat([local, localNameBytes, localExtraBytes, payloadBytes])
  const central = Buffer.alloc(46)
  central.writeUInt32LE(0x02014b50, 0)
  central.writeUInt16LE(20, 4)
  central.writeUInt16LE(20, 6)
  central.writeUInt32LE(payloadBytes.length, 20)
  central.writeUInt32LE(payloadBytes.length, 24)
  central.writeUInt16LE(centralNameBytes.length, 28)
  central.writeUInt16LE(centralExtraBytes.length, 30)
  central.writeUInt16LE(entryCommentBytes.length, 32)
  const centralRecord = Buffer.concat([
    central,
    centralNameBytes,
    centralExtraBytes,
    entryCommentBytes,
  ])
  const end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50, 0)
  end.writeUInt16LE(1, 8)
  end.writeUInt16LE(1, 10)
  end.writeUInt32LE(centralRecord.length, 12)
  end.writeUInt32LE(localRecord.length, 16)
  end.writeUInt16LE(archiveCommentBytes.length, 20)
  return Buffer.concat([localRecord, centralRecord, end, archiveCommentBytes])
}

describe('E2E artifact secret scanner', () => {
  it('rejects exact supplied secrets without treating ordinary words as secrets', () => {
    expect(scanText('prefix exact-secret-123 suffix', ['exact-secret-123'])).toThrow('secret scan')
    expect(
      scanText('{"message":"secret scan passed","token_count":2,"keyboard_key":"Enter"}'),
    ).not.toThrow()
  })

  it('rejects recursively structured headers, cookies, and token fields', () => {
    expect(
      scanText('{"events":[{"headers":[{"name":"authorization","value":"opaque-value-123"}]}]}'),
    ).toThrow('secret scan')
    expect(
      scanText('{"context":{"cookies":[{"name":"moldy_at","value":"session-value-123"}]}}'),
    ).toThrow('secret scan')
    expect(
      scanText('{"context":{"cookies":[{"name":"preferences","value":"dark-mode"}]}}'),
    ).toThrow('secret scan')
    expect(scanText('{"response":{"refresh_token":"refresh-value-123"}}')).toThrow('secret scan')
    expect(
      scanText('{"context":{"cookies":[]},"headers":{"content-type":"text/plain"}}'),
    ).not.toThrow()
  })

  it('rejects credential query parameters and credential/session formats', () => {
    expect(
      scanText('{"url":"https://example.test/callback?code=public&access_token=hidden-value"}'),
    ).toThrow('secret scan')
    expect(scanText('{"url":"https://storage.test/object?X-Amz-Signature=hidden-value"}')).toThrow(
      'secret scan',
    )
    expect(scanText('-----BEGIN PRIVATE KEY-----\nopaque\n-----END PRIVATE KEY-----')).toThrow(
      'secret scan',
    )
    expect(scanText('eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.signature_value_123')).toThrow(
      'secret scan',
    )
    expect(scanText('moldy_rt=session-value-123')).toThrow('secret scan')
  })

  it('recursively decodes base64 and JSON content representations', () => {
    const encoded = Buffer.from('{"payload":"authorization: Bearer hidden-token-123"}').toString(
      'base64',
    )
    expect(scanText(JSON.stringify({ response: { content: encoded } }))).toThrow('secret scan')
    const nested = JSON.stringify({ body: JSON.stringify({ api_key: 'hidden-value-123' }) })
    expect(scanText(nested)).toThrow('secret scan')
  })

  it('bounds structured depth and decoded representation size', () => {
    let nested = '"safe"'
    for (let depth = 0; depth < 40; depth += 1) nested = `{"content":${nested}}`
    expect(scanText(nested)).toThrow('secret scan')
    const oversized = Buffer.alloc(5 * 1024 * 1024 + 1).toString('base64')
    expect(scanText(JSON.stringify({ content: oversized }))).toThrow('secret scan')
  })

  it('scans structured secrets inside trace ZIP entries', async () => {
    const archive = new JSZip()
    archive.file('trace.network', '{"headers":{"cookie":"moldy_at=session-value-123"}}')
    const content = await archive.generateAsync({ type: 'nodebuffer' })
    expect(() => scanTraceZip(content, [])).toThrow('secret scan')
  })

  it('rejects exact secrets in ZIP filenames', () => {
    expect(() =>
      scanTraceZip(storedZip({ localName: 'configured-secret.txt' }), ['configured-secret']),
    ).toThrow('secret scan')
  })

  it('rejects exact secrets in ZIP entry comments', () => {
    expect(() =>
      scanTraceZip(storedZip({ entryComment: 'configured-secret' }), ['configured-secret']),
    ).toThrow('secret scan')
  })

  it('rejects exact secrets independently in ZIP local and central extras', () => {
    expect(() =>
      scanTraceZip(
        storedZip({ localExtra: 'configured-secret', centralExtra: 'safe-central-extra' }),
        ['configured-secret'],
      ),
    ).toThrow('secret scan')
    expect(() =>
      scanTraceZip(
        storedZip({ localExtra: 'safe-local-extra', centralExtra: 'configured-secret' }),
        ['configured-secret'],
      ),
    ).toThrow('secret scan')
  })

  it('rejects exact secrets in ZIP archive comments', () => {
    expect(() =>
      scanTraceZip(storedZip({ archiveComment: 'configured-secret' }), ['configured-secret']),
    ).toThrow('secret scan')
  })

  it('rejects mismatched local and central ZIP metadata', () => {
    expect(() => scanTraceZip(storedZip({ centralName: 'other.txt' }), [])).toThrow('invalid')
  })

  it('rejects forged inflate sizes before an oversized allocation', async () => {
    const archive = new JSZip()
    archive.file('bomb.txt', Buffer.alloc(10 * 1024 * 1024 + 1), { compression: 'DEFLATE' })
    const content = await archive.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' })
    const central = content.indexOf(Buffer.from([0x50, 0x4b, 0x01, 0x02]))
    content.writeUInt32LE(1, central + 24)
    content.writeUInt16LE(content.readUInt16LE(central + 8) | 8, central + 8)
    content.writeUInt16LE(content.readUInt16LE(6) | 8, 6)
    expect(() => scanTraceZip(content, [])).toThrow('scan limit')
  })

  it('rejects invalid central-directory offsets and unsupported methods', async () => {
    const archive = new JSZip()
    archive.file('trace.txt', 'safe trace')
    const invalidOffset = await archive.generateAsync({ type: 'nodebuffer' })
    const end = invalidOffset.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06]))
    invalidOffset.writeUInt32LE(1, end + 16)
    expect(() => scanTraceZip(invalidOffset, [])).toThrow('invalid')

    const invalidMethod = await archive.generateAsync({ type: 'nodebuffer' })
    const central = invalidMethod.indexOf(Buffer.from([0x50, 0x4b, 0x01, 0x02]))
    invalidMethod.writeUInt16LE(99, central + 10)
    expect(() => scanTraceZip(invalidMethod, [])).toThrow('scan limit')
  })
})
