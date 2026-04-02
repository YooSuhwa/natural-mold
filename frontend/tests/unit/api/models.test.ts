import { describe, it, expect } from "vitest"
import { modelsApi } from "@/lib/api/models"
import { mockModelList } from "../../mocks/fixtures"

describe("modelsApi", () => {
  it("list() returns all models", async () => {
    const models = await modelsApi.list()
    expect(models).toEqual(mockModelList)
    expect(models).toHaveLength(2)
  })

  it("create() sends POST and returns new model", async () => {
    const model = await modelsApi.create({
      provider: "openai",
      model_name: "gpt-4o-mini",
      display_name: "GPT-4o Mini",
    })
    expect(model.id).toBe("model-new")
    expect(model.provider).toBe("openai")
    expect(model.model_name).toBe("gpt-4o-mini")
    expect(model.display_name).toBe("GPT-4o Mini")
  })

  it("delete() sends DELETE and returns undefined", async () => {
    const result = await modelsApi.delete("model-1")
    expect(result).toBeUndefined()
  })

  it("list() returns models with correct provider info", async () => {
    const models = await modelsApi.list()
    expect(models[0].provider).toBe("openai")
    expect(models[1].provider).toBe("anthropic")
    expect(models[0].is_default).toBe(true)
    expect(models[1].is_default).toBe(false)
  })
})
