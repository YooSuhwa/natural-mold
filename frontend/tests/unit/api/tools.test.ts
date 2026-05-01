import { describe, it, expect } from 'vitest'
import { toolsApi } from '@/lib/api/tools'
import { mockToolList } from '../../mocks/fixtures'

describe('toolsApi', () => {
  it('list() returns all tools', async () => {
    const tools = await toolsApi.list()
    expect(tools).toEqual(mockToolList)
    expect(tools).toHaveLength(2)
  })

  it('create() sends POST and returns new tool', async () => {
    // ``createCustom``은 ``create``로 통합됨 (custom/prebuilt 분기는 백엔드의
    // definition_key로 결정). custom 시나리오를 그대로 검증.
    const tool = await toolsApi.create({
      definition_key: 'custom_http',
      name: 'My API',
      config: {
        api_url: 'https://example.com/api',
        http_method: 'GET',
      },
    } as unknown as Parameters<typeof toolsApi.create>[0])
    expect(tool.id).toBe('tool-new')
    expect(tool.type).toBe('custom')
    expect(tool.is_system).toBe(false)
    expect(tool.name).toBe('My API')
  })

  it('update() sends PATCH and returns tool with connection_id', async () => {
    const tool = await toolsApi.update('tool-1', { connection_id: 'conn-custom-1' })
    expect(tool.id).toBe('tool-1')
    expect(tool.connection_id).toBe('conn-custom-1')
  })

  it('delete() sends DELETE and returns undefined', async () => {
    const result = await toolsApi.delete('tool-1')
    expect(result).toBeUndefined()
  })

  it('list() returns tools with correct types', async () => {
    const tools = await toolsApi.list()
    expect(tools[0].type).toBe('prebuilt')
    expect(tools[0].is_system).toBe(true)
    expect(tools[1].type).toBe('custom')
    expect(tools[1].is_system).toBe(false)
  })
})
