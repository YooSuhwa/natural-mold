import { describe, it, expect } from "vitest"
import { createStore } from "jotai"
import { sidebarOpenAtom } from "@/lib/stores/sidebar-store"

describe("sidebar-store atoms", () => {
  it("sidebarOpenAtom defaults to true", () => {
    const store = createStore()
    expect(store.get(sidebarOpenAtom)).toBe(true)
  })

  it("sidebarOpenAtom can be toggled", () => {
    const store = createStore()
    store.set(sidebarOpenAtom, false)
    expect(store.get(sidebarOpenAtom)).toBe(false)
    store.set(sidebarOpenAtom, true)
    expect(store.get(sidebarOpenAtom)).toBe(true)
  })
})
