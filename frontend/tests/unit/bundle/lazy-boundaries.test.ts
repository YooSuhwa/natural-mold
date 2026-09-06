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
    const assistantMessagePartsSource = readFrontendFile(
      'src/components/chat/assistant-message-parts.tsx',
    )
    const streamingPluginSource = readFrontendFile(
      'src/components/chat/markdown-streaming-plugins.ts',
    )
    const streamingSurfaceSource = `${assistantThreadSource}\n${assistantMessagePartsSource}`

    expect(assistantMessagePartsSource).toContain('markdown-streaming-plugins')
    expect(streamingSurfaceSource).not.toContain("from '@/components/chat/markdown-plugins'")
    expect(streamingPluginSource).not.toContain('remark-math')
  })

  it('loads builder overrides only through a lazy boundary', () => {
    const assistantThreadSource = readFrontendFile('src/components/chat/assistant-thread.tsx')
    const builderBoundarySource = readFrontendFile(
      'src/components/chat/assistant-builder-overrides.tsx',
    )
    const threadRendererSource = readFrontendFile(
      'src/components/chat/assistant-thread-message-renderers.tsx',
    )
    const assistantSurfaceSource = [
      assistantThreadSource,
      threadRendererSource,
      builderBoundarySource,
    ].join('\n')

    expect(assistantSurfaceSource).not.toMatch(
      /import\s*\{[\s\S]*BuilderAssistantMessage[\s\S]*\}\s*from ['"]@\/components\/chat\/builder-overrides['"]/,
    )
    expect(builderBoundarySource.match(/lazy\(\(\) =>/g)).toHaveLength(5)
    expect(builderBoundarySource).toContain('builder-overrides')
  })

  it('keeps raw artifact and HITL payload fields out of assistant thread wrappers', () => {
    const wrapperSources = [
      readFrontendFile('src/components/chat/assistant-thread.tsx'),
      readFrontendFile('src/components/chat/assistant-thread-message-renderers.tsx'),
      readFrontendFile('src/components/chat/assistant-message-artifacts.tsx'),
    ].join('\n')

    expect(wrapperSources).not.toMatch(
      /storage_path|download_url|raw_data|interrupt\.value|JSON\.stringify/,
    )
  })

  it('loads zip and artifact data parsers only when those features are used', () => {
    const skillDialogSource = readFrontendFile('src/components/skill/skill-create-dialog.tsx')
    const skillTabsSource = readFrontendFile('src/components/skill/skill-create-tabs.tsx')
    const skillsApiSource = readFrontendFile('src/lib/api/skills.ts')
    const dataPreviewSource = readFrontendFile(
      'src/components/chat/artifacts/data-preview-utils.ts',
    )
    const skillCreateUploadSource = [skillDialogSource, skillTabsSource, skillsApiSource].join('\n')

    expect(skillCreateUploadSource).not.toContain('jszip')
    expect(skillsApiSource).toContain("apiUpload<Skill>('/api/skills/upload'")
    expect(dataPreviewSource).not.toContain("from 'csv-parse/browser/esm/sync'")
    expect(dataPreviewSource).not.toContain("from 'smol-toml'")
    expect(dataPreviewSource).not.toContain("from 'yaml'")
  })
})
