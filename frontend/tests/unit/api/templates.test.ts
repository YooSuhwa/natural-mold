import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../setup'
import { templatesApi } from '@/lib/api/templates'
import { mockTemplateList, mockTemplate } from '../../mocks/fixtures'

const API_BASE = 'http://localhost:8001'

describe('templatesApi', () => {
  it('list() returns all templates', async () => {
    const templates = await templatesApi.list()
    expect(templates).toEqual(mockTemplateList)
    expect(templates).toHaveLength(2)
  })

  it('list() with category sends query parameter', async () => {
    let capturedUrl = ''
    server.use(
      http.get(`${API_BASE}/api/templates`, ({ request }) => {
        capturedUrl = request.url
        return HttpResponse.json([mockTemplate])
      }),
    )

    const templates = await templatesApi.list('productivity')
    expect(capturedUrl).toContain('category=productivity')
    expect(templates).toHaveLength(1)
  })

  it('get() returns a single template by id', async () => {
    const template = await templatesApi.get('template-1')
    expect(template.id).toBe('template-1')
    expect(template.name).toBe(mockTemplate.name)
    expect(template.category).toBe('productivity')
  })
})
