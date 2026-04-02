import { render, screen, userEvent } from "../../test-utils"
import { PrebuiltAuthDialog } from "@/components/tool/prebuilt-auth-dialog"
import type { Tool } from "@/lib/types"

const mockMutate = vi.fn()

vi.mock("@/lib/hooks/use-tools", () => ({
  useUpdateToolAuthConfig: () => ({
    mutate: mockMutate,
    isPending: false,
  }),
}))

const naverTool: Tool = {
  id: "tool-naver",
  type: "prebuilt",
  is_system: true,
  mcp_server_id: null,
  name: "Naver 검색",
  description: "네이버 검색 API",
  parameters_schema: null,
  api_url: null,
  http_method: null,
  auth_type: null,
  auth_config: null,
  server_key_available: false,
  created_at: "2026-01-01T00:00:00Z",
}

const googleSearchTool: Tool = {
  ...naverTool,
  id: "tool-google",
  name: "Google 검색",
  auth_config: { google_api_key: "existing-key" },
}

function renderDialog(tool: Tool = naverTool) {
  return render(
    <PrebuiltAuthDialog
      tool={tool}
      trigger={<button type="button">키 설정</button>}
    />
  )
}

describe("PrebuiltAuthDialog", () => {
  beforeEach(() => {
    mockMutate.mockClear()
  })

  it("opens dialog with tool info when trigger clicked", async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText("키 설정"))
    expect(screen.getByText("Naver 검색 API 키 설정")).toBeInTheDocument()
    expect(
      screen.getByText("이 도구를 사용하려면 API 키를 설정하세요. 네이버 개발자센터에서 발급받을 수 있습니다.")
    ).toBeInTheDocument()
  })

  it("shows Naver-specific fields for Naver tool", async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText("키 설정"))
    expect(screen.getByText("Client ID")).toBeInTheDocument()
    expect(screen.getByText("Client Secret")).toBeInTheDocument()
  })

  it("shows Google-specific fields for Google Search tool", async () => {
    const user = userEvent.setup()
    renderDialog(googleSearchTool)
    await user.click(screen.getByText("키 설정"))
    expect(screen.getByText("API Key")).toBeInTheDocument()
    expect(screen.getByText("Search Engine ID")).toBeInTheDocument()
  })

  it("shows existing config indicator when tool has auth_config", async () => {
    const user = userEvent.setup()
    renderDialog(googleSearchTool)
    await user.click(screen.getByText("키 설정"))
    expect(
      screen.getByText("API 키가 설정되어 있습니다")
    ).toBeInTheDocument()
  })

  it("has save and cancel buttons", async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText("키 설정"))
    expect(screen.getByText("저장")).toBeInTheDocument()
    expect(screen.getByText("취소")).toBeInTheDocument()
  })

  it("calls mutate when save is clicked", async () => {
    const user = userEvent.setup()
    renderDialog()
    await user.click(screen.getByText("키 설정"))
    await user.click(screen.getByText("저장"))
    expect(mockMutate).toHaveBeenCalledWith(
      { id: "tool-naver", authConfig: {} },
      expect.any(Object)
    )
  })

  it("shows no fields for unknown provider", async () => {
    const unknownTool: Tool = {
      ...naverTool,
      name: "Unknown Tool",
    }
    const user = userEvent.setup()
    render(
      <PrebuiltAuthDialog
        tool={unknownTool}
        trigger={<button type="button">키 설정</button>}
      />
    )
    await user.click(screen.getByText("키 설정"))
    // No specific fields should appear (no Client ID, API Key, etc)
    expect(screen.queryByText("Client ID")).not.toBeInTheDocument()
    expect(screen.queryByText("Search Engine ID")).not.toBeInTheDocument()
  })

  it("shows Google workspace fields for Gmail tool", async () => {
    const gmailTool: Tool = {
      ...naverTool,
      id: "tool-gmail",
      name: "Gmail 전송",
    }
    const user = userEvent.setup()
    render(
      <PrebuiltAuthDialog
        tool={gmailTool}
        trigger={<button type="button">키 설정</button>}
      />
    )
    await user.click(screen.getByText("키 설정"))
    expect(screen.getByText("OAuth Client ID")).toBeInTheDocument()
    expect(screen.getByText("OAuth Client Secret")).toBeInTheDocument()
    expect(screen.getByText("Refresh Token")).toBeInTheDocument()
  })

  it("shows webhook URL field for Google Chat tool", async () => {
    const chatTool: Tool = {
      ...naverTool,
      id: "tool-chat",
      name: "Google Chat Webhook",
    }
    const user = userEvent.setup()
    render(
      <PrebuiltAuthDialog
        tool={chatTool}
        trigger={<button type="button">키 설정</button>}
      />
    )
    await user.click(screen.getByText("키 설정"))
    expect(screen.getByText("Webhook URL")).toBeInTheDocument()
  })
})
