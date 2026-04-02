import { describe, it, expect } from "vitest"
import { toolsApi } from "@/lib/api/tools"
import { mockToolList, mockMCPServer } from "../../mocks/fixtures"

describe("toolsApi", () => {
  it("list() returns all tools", async () => {
    const tools = await toolsApi.list()
    expect(tools).toEqual(mockToolList)
    expect(tools).toHaveLength(2)
  })

  it("createCustom() sends POST and returns new custom tool", async () => {
    const tool = await toolsApi.createCustom({
      name: "My API",
      api_url: "https://example.com/api",
      http_method: "GET",
    })
    expect(tool.id).toBe("tool-new")
    expect(tool.type).toBe("custom")
    expect(tool.is_system).toBe(false)
    expect(tool.name).toBe("My API")
    expect(tool.api_url).toBe("https://example.com/api")
  })

  it("registerMCPServer() sends POST and returns MCPServer", async () => {
    const server = await toolsApi.registerMCPServer({
      name: "My MCP",
      url: "http://localhost:9999",
    })
    expect(server.id).toBe("mcp-new")
    expect(server.name).toBe("My MCP")
    expect(server.url).toBe("http://localhost:9999")
  })

  it("testMCPConnection() returns success result", async () => {
    const result = await toolsApi.testMCPConnection("mcp-1")
    expect(result.success).toBe(true)
    expect(result.tools).toHaveLength(1)
  })

  it("updateAuthConfig() sends PATCH and returns updated tool", async () => {
    const tool = await toolsApi.updateAuthConfig("tool-1", { api_key: "new-key" })
    expect(tool.id).toBe("tool-1")
    expect(tool.auth_config).toEqual({ api_key: "***" })
  })

  it("delete() sends DELETE and returns undefined", async () => {
    const result = await toolsApi.delete("tool-1")
    expect(result).toBeUndefined()
  })

  it("list() returns tools with correct types", async () => {
    const tools = await toolsApi.list()
    expect(tools[0].type).toBe("prebuilt")
    expect(tools[0].is_system).toBe(true)
    expect(tools[1].type).toBe("custom")
    expect(tools[1].is_system).toBe(false)
  })
})
