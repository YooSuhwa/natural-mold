import { describe, it, expect } from "vitest"
import { agentsApi } from "@/lib/api/agents"
import { mockAgentList, mockAgent } from "../../mocks/fixtures"

describe("agentsApi", () => {
  it("list() returns all agents", async () => {
    const agents = await agentsApi.list()
    expect(agents).toEqual(mockAgentList)
    expect(agents).toHaveLength(2)
  })

  it("get() returns a single agent by id", async () => {
    const agent = await agentsApi.get("agent-1")
    expect(agent.id).toBe("agent-1")
    expect(agent.name).toBe(mockAgent.name)
  })

  it("create() sends POST and returns the new agent", async () => {
    const agent = await agentsApi.create({
      name: "New Agent",
      system_prompt: "You are new.",
      model_id: "model-1",
    })
    expect(agent.id).toBe("agent-new")
    expect(agent.name).toBe("New Agent")
    expect(agent.system_prompt).toBe("You are new.")
  })

  it("update() sends PUT and returns the updated agent", async () => {
    const agent = await agentsApi.update("agent-1", {
      name: "Updated Agent",
    })
    expect(agent.id).toBe("agent-1")
    expect(agent.name).toBe("Updated Agent")
  })

  it("delete() sends DELETE and returns undefined", async () => {
    const result = await agentsApi.delete("agent-1")
    expect(result).toBeUndefined()
  })
})
