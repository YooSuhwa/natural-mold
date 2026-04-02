import { describe, it, expect } from "vitest"
import { triggersApi } from "@/lib/api/triggers"
import { mockTriggerList } from "../../mocks/fixtures"

describe("triggersApi", () => {
  it("list() returns triggers for an agent", async () => {
    const triggers = await triggersApi.list("agent-1")
    expect(triggers).toEqual(mockTriggerList)
    expect(triggers).toHaveLength(2)
  })

  it("create() sends POST and returns new trigger", async () => {
    const trigger = await triggersApi.create("agent-1", {
      trigger_type: "interval",
      schedule_config: { interval_minutes: 30 },
      input_message: "Check status",
    })
    expect(trigger.id).toBe("trigger-new")
    expect(trigger.agent_id).toBe("agent-1")
    expect(trigger.trigger_type).toBe("interval")
    expect(trigger.input_message).toBe("Check status")
  })

  it("update() sends PUT and returns updated trigger", async () => {
    const trigger = await triggersApi.update("agent-1", "trigger-1", {
      input_message: "Updated message",
    })
    expect(trigger.id).toBe("trigger-1")
    expect(trigger.agent_id).toBe("agent-1")
    expect(trigger.input_message).toBe("Updated message")
  })

  it("delete() sends DELETE and returns undefined", async () => {
    const result = await triggersApi.delete("agent-1", "trigger-1")
    expect(result).toBeUndefined()
  })

  it("list() returns triggers with correct types", async () => {
    const triggers = await triggersApi.list("agent-1")
    expect(triggers[0].trigger_type).toBe("interval")
    expect(triggers[0].status).toBe("active")
    expect(triggers[1].trigger_type).toBe("cron")
    expect(triggers[1].status).toBe("paused")
  })
})
