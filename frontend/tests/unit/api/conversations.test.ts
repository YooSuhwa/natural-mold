import { describe, it, expect } from "vitest"
import { conversationsApi } from "@/lib/api/conversations"
import {
  mockConversationList,
  mockConversation,
  mockMessageList,
} from "../../mocks/fixtures"

describe("conversationsApi", () => {
  it("list() returns conversations for an agent", async () => {
    const conversations = await conversationsApi.list("agent-1")
    expect(conversations).toEqual(mockConversationList)
    expect(conversations).toHaveLength(2)
  })

  it("create() sends POST with title and returns new conversation", async () => {
    const conversation = await conversationsApi.create("agent-1", "My Chat")
    expect(conversation.id).toBe("conv-new")
    expect(conversation.agent_id).toBe("agent-1")
    expect(conversation.title).toBe("My Chat")
  })

  it("create() works without title", async () => {
    const conversation = await conversationsApi.create("agent-1")
    expect(conversation.id).toBe("conv-new")
    expect(conversation.agent_id).toBe("agent-1")
  })

  it("messages() returns messages for a conversation", async () => {
    const messages = await conversationsApi.messages("conv-1")
    expect(messages).toEqual(mockMessageList)
    expect(messages).toHaveLength(2)
    expect(messages[0].role).toBe("user")
    expect(messages[1].role).toBe("assistant")
  })

  it("messages() returns correct content", async () => {
    const messages = await conversationsApi.messages("conv-1")
    expect(messages[0].content).toBe("Hello, how are you?")
    expect(messages[1].content).toBe("I'm doing great! How can I help?")
  })
})
