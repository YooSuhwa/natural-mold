import { describe, expect, it } from 'vitest'

import {
  KNOWN_SHIM_BEACON_URL,
  unexpectedMcpAppsBrowserErrors,
  type ConsoleErrorObservation,
  type RequestFailureObservation,
} from '../../e2e/helpers/mcp-apps-network-errors'

const APP_ORIGIN = 'http://localhost:3100'
const knownConsoleError: ConsoleErrorObservation = {
  text: 'Failed to load resource: net::ERR_NAME_NOT_RESOLVED',
  locationUrl: KNOWN_SHIM_BEACON_URL,
}
const knownRequestFailure: RequestFailureObservation = {
  url: KNOWN_SHIM_BEACON_URL,
  errorText: 'net::ERR_NAME_NOT_RESOLVED',
  method: 'GET',
  resourceType: 'script',
  frameUrl: `https://fixture.scf.auiusercontent.com/hash/shim.html?origin=${APP_ORIGIN}`,
}

describe('MCP Apps optional shim telemetry classifier', () => {
  it('accepts a healthy run with no browser errors', () => {
    expect(unexpectedMcpAppsBrowserErrors([], [], APP_ORIGIN)).toEqual({
      consoleErrors: [],
      requestFailures: [],
    })
  })

  it('accepts only the exact known optional beacon failure', () => {
    expect(
      unexpectedMcpAppsBrowserErrors([knownConsoleError], [knownRequestFailure], APP_ORIGIN),
    ).toEqual({ consoleErrors: [], requestFailures: [] })
  })

  it('rejects an unrelated DNS failure even when the aggregate category is identical', () => {
    const unrelated = {
      ...knownRequestFailure,
      url: 'https://unrelated.invalid/beacon.js',
    }
    expect(unexpectedMcpAppsBrowserErrors([], [unrelated], APP_ORIGIN).requestFailures).toEqual([
      unrelated,
    ])
  })

  it('accepts canceled same-origin Next.js RSC prefetches from the app frame', () => {
    const prefetch: RequestFailureObservation = {
      url: `${APP_ORIGIN}/marketplace?_rsc=fixture`,
      errorText: 'net::ERR_ABORTED',
      method: 'GET',
      resourceType: 'fetch',
      frameUrl: `${APP_ORIGIN}/agents/agent-1/conversations/conversation-1`,
    }

    expect(unexpectedMcpAppsBrowserErrors([], [prefetch], APP_ORIGIN).requestFailures).toEqual([])
  })
})
