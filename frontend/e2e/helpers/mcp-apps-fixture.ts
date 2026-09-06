import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { once } from 'node:events'
import { access } from 'node:fs/promises'
import { createServer, createConnection } from 'node:net'
import { resolve } from 'node:path'

const FIXTURE_START_TIMEOUT_MS = 10_000
const FIXTURE_STOP_TIMEOUT_MS = 5_000

export interface McpAppsFixture {
  readonly url: string
  readonly stop: () => Promise<void>
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
