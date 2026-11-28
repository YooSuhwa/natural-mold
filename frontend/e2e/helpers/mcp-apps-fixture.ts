import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { once } from 'node:events'
import { access } from 'node:fs/promises'
import { createServer, createConnection } from 'node:net'
import { resolve } from 'node:path'
import type { Page } from '@playwright/test'

const FIXTURE_START_TIMEOUT_MS = 10_000
const FIXTURE_STOP_TIMEOUT_MS = 5_000

export interface McpAppsFixture {
  readonly url: string
  readonly stop: () => Promise<void>
}

/**
 * Serve the unmodified 0.0.29 shim from the versioned URL constructed at
 * https://github.com/assistant-ui/assistant-ui/blob/d511ff496c59005f5df4df155dcccb3c1ddff475/packages/safe-content-frame/src/index.ts
 * Its deployment response, without CDN-injected analytics, has SHA-256
 * 9541e112abff415a7f54d6a9d57292d532731e55e9a3b9cdcbd3d631994b1ac4.
 */
export async function servePinnedMcpAppShim(page: Page, appOrigin: string): Promise<() => number> {
  const shimFile = resolve(process.cwd(), 'e2e/fixtures/safe-content-frame/shim.html')
  const expectedSearch = new URLSearchParams({ origin: appOrigin }).toString()
  // h184756 is the published shim version selected by safe-content-frame 0.0.29.
  // Only the transport of this static document is replaced. Its origin and
  // content-hash checks and the widget's real server-side proxy stay active.
  let served = 0
  await page.route(
    (url) =>
      /^https:\/\/[a-z0-9]{50}-h184756\.scf\.auiusercontent\.com$/.test(url.origin) &&
      url.pathname === '/moldy-mcp-app/shim.html' &&
      url.search === `?${expectedSearch}`,
    async (route) => {
      const request = route.request()
      if (request.method() !== 'GET' || !request.isNavigationRequest()) return route.fallback()
      await route.fulfill({ path: shimFile, contentType: 'text/html; charset=utf-8' })
      served += 1
    },
  )
  return () => served
}

async function freeLoopbackPort(): Promise<number> {
  const server = createServer()
  await new Promise<void>((resolveListen, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => resolveListen())
  })
  const address = server.address()
  if (!address || typeof address === 'string') {
    server.close()
    throw new Error('MCP Apps fixture could not allocate a loopback port')
  }
  await new Promise<void>((resolveClose, reject) =>
    server.close((error) => (error ? reject(error) : resolveClose())),
  )
  return address.port
}

async function portIsOpen(port: number): Promise<boolean> {
  return new Promise((resolveOpen) => {
    const socket = createConnection({ host: '127.0.0.1', port })
    socket.setTimeout(100)
    socket.once('connect', () => {
      socket.destroy()
      resolveOpen(true)
    })
    const closed = () => {
      socket.destroy()
      resolveOpen(false)
    }
    socket.once('error', closed)
    socket.once('timeout', closed)
  })
}

async function waitForPort(port: number, child: ChildProcessWithoutNullStreams): Promise<void> {
  const deadline = Date.now() + FIXTURE_START_TIMEOUT_MS
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error('MCP Apps fixture exited before startup')
    if (await portIsOpen(port)) return
    await new Promise((resolveWait) => setTimeout(resolveWait, 50))
  }
  throw new Error('MCP Apps fixture did not open its loopback port')
}

async function stopChild(child: ChildProcessWithoutNullStreams): Promise<void> {
  if (child.exitCode !== null) return
  child.kill('SIGTERM')
  const exited = once(child, 'exit')
  const timeout = new Promise<'timeout'>((resolveTimeout) =>
    setTimeout(() => resolveTimeout('timeout'), FIXTURE_STOP_TIMEOUT_MS),
  )
  if ((await Promise.race([exited, timeout])) === 'timeout' && child.exitCode === null) {
    child.kill('SIGKILL')
    await once(child, 'exit')
  }
}

export async function startMcpAppsFixture(): Promise<McpAppsFixture> {
  const backendRoot = process.env.MOLDY_BACKEND_SOURCE_ROOT ?? resolve(process.cwd(), '../backend')
  const python =
    process.env.E2E_BACKEND_PYTHON ??
    process.env.MOLDY_GATE_PYTHON ??
    resolve(backendRoot, '.venv/bin/python')
  const fixture = resolve(backendRoot, 'tests/fixtures/mcp_apps_server.py')
  await Promise.all([access(python), access(fixture)])
  const port = await freeLoopbackPort()
  const child = spawn(python, [fixture, '--port', String(port)], {
    cwd: backendRoot,
    env: {
      NODE_ENV: process.env.NODE_ENV ?? 'test',
      PATH: process.env.PATH ?? '',
      PYTHONUNBUFFERED: '1',
    },
    stdio: 'pipe',
  })
  child.stdin.end()
  let stderr = ''
  child.stderr.setEncoding('utf8')
  child.stderr.on('data', (chunk: string) => {
    stderr = `${stderr}${chunk}`.slice(-2_000)
  })
  try {
    await waitForPort(port, child)
  } catch (error: unknown) {
    await stopChild(child)
    const detail = stderr.trim() ? `: ${stderr.trim()}` : ''
    throw new Error(`Failed to start MCP Apps fixture${detail}`, { cause: error })
  }
  return {
    url: `http://127.0.0.1:${port}/mcp`,
    stop: () => stopChild(child),
  }
}
