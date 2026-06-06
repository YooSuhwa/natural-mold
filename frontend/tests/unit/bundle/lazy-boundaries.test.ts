import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '../../..')

function readFrontendFile(relativePath: string): string {
  return readFileSync(resolve(root, relativePath), 'utf8')
}

describe('frontend lazy-load boundaries', () => {
  it('keeps syntax highlighting out of the shared markdown entrypoint', () => {
    const markdownSource = readFrontendFile('src/components/chat/markdown-content.tsx')

    expect(markdownSource).not.toContain('react-syntax-highlighter')
    expect(markdownSource).not.toContain('oneDark')
    expect(existsSync(resolve(root, 'src/components/chat/markdown-code-highlighter.tsx'))).toBe(
      true,
    )
  })

  it('keeps image-only rendering out of the markdown parser module', () => {
    const markdownSource = readFrontendFile('src/components/chat/markdown-content.tsx')
    const toolFallbackSource = readFrontendFile('src/components/chat/tool-ui/generic-tool-ui.tsx')
    const toolResultSource = readFrontendFile(
      'src/components/chat/right-rail/tool-result-panel-content.tsx',
    )

    expect(markdownSource).not.toContain('export function ChatImage')
    expect(existsSync(resolve(root, 'src/components/chat/chat-image.tsx'))).toBe(true)
    expect(toolFallbackSource).toContain('@/components/chat/chat-image')
    expect(toolResultSource).toContain('@/components/chat/chat-image')
  })

  it('does not load final-only markdown plugins in streaming chat', () => {
    const assistantThreadSource = readFrontendFile('src/components/chat/assistant-thread.tsx')
    const streamingPluginSource = readFrontendFile(
      'src/components/chat/markdown-streaming-plugins.ts',
    )

    expect(assistantThreadSource).toContain('markdown-streaming-plugins')
    expect(assistantThreadSource).not.toContain('markdown-plugins')
    expect(streamingPluginSource).not.toContain('remark-math')
  })

  it('loads builder overrides only through a lazy boundary', () => {
    const assistantThreadSource = readFrontendFile('src/components/chat/assistant-thread.tsx')

    expect(assistantThreadSource).not.toMatch(
      /import\s*\{[\s\S]*BuilderAssistantMessage[\s\S]*\}\s*from ['"]@\/components\/chat\/builder-overrides['"]/,
    )
    expect(assistantThreadSource).toContain('lazy(')
    expect(assistantThreadSource).toContain('builder-overrides')
  })

  it('loads zip and artifact data parsers only when those features are used', () => {
    const skillDialogSource = readFrontendFile('src/components/skill/skill-create-dialog.tsx')
    const dataPreviewSource = readFrontendFile(
      'src/components/chat/artifacts/data-preview-utils.ts',
    )

    expect(skillDialogSource).not.toContain("import JSZip from 'jszip'")
    expect(skillDialogSource).toContain("import('jszip')")
    expect(dataPreviewSource).not.toContain("from 'csv-parse/browser/esm/sync'")
    expect(dataPreviewSource).not.toContain("from 'smol-toml'")
    expect(dataPreviewSource).not.toContain("from 'yaml'")
  })
})
