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
    const tool = await toolsApi.create({
      definition_key: 'custom_http',
      name: 'My API',
      parameters: {
        api_url: 'https://example.com/api',
        http_method: 'GET',
      },
    })
    expect(tool.id).toBe('tool-new')
    expect(tool.definition_key).toBe('custom_http')
    expect(tool.parameters).toEqual({
      api_url: 'https://example.com/api',
      http_method: 'GET',
    })
    expect(tool.name).toBe('My API')
  })

  it('update() sends PATCH and returns the updated credential binding', async () => {
    const tool = await toolsApi.update('tool-1', { credential_id: 'cred-1' })
    expect(tool.id).toBe('tool-1')
    expect(tool.credential_id).toBe('cred-1')
  })

  it('delete() sends DELETE and returns undefined', async () => {
    const result = await toolsApi.delete('tool-1')
    expect(result).toBeUndefined()
  })

  it('list() returns canonical tool instance fields', async () => {
    const tools = await toolsApi.list()
    expect(tools[0].definition_key).toBe('web_search')
    expect(tools[0].enabled).toBe(true)
    expect(tools[1].definition_key).toBe('custom_http')
    expect(tools[1].parameters).toEqual({
      api_url: 'https://example.com/api',
      http_method: 'POST',
    })
  })
})
