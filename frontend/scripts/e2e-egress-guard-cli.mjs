import {
  E2EEgressPolicyError,
  parsePositiveInteger,
  writeExclusiveJson,
} from './e2e-egress-policy.mjs'

const REQUIRED_ENVIRONMENT = Object.freeze([
  'E2E_EGRESS_UPSTREAM_BASE_URL',
  'E2E_EGRESS_UPSTREAM_API_KEY',
  'E2E_EGRESS_PROXY_TOKEN',
  'E2E_EGRESS_READY_FILE',
  'E2E_EGRESS_RECEIPT_FILE',
])

function requireCliEnvironment(environment) {
  if (
    !REQUIRED_ENVIRONMENT.every(
      (name) => typeof environment[name] === 'string' && environment[name].length > 0,
    )
  ) {
    throw new E2EEgressPolicyError('missing_cli_environment')
  }
  return environment
}

export async function runE2EEgressGuardCli(
  startGuard,
  environment = process.env,
  runtime = process,
) {
  const env = requireCliEnvironment(environment)
  const guard = await startGuard({
    configuredBaseUrl: env.E2E_EGRESS_UPSTREAM_BASE_URL,
    upstreamApiKey: env.E2E_EGRESS_UPSTREAM_API_KEY,
    proxyToken: env.E2E_EGRESS_PROXY_TOKEN,
    port: parsePositiveInteger(env.E2E_EGRESS_PORT, 0, 'port'),
    allowOwnedLoopback: env.E2E_EGRESS_ALLOW_OWNED_LOOPBACK === '1',
  })
  try {
    writeExclusiveJson(env.E2E_EGRESS_READY_FILE, {
      ready: true,
      proxyBaseUrl: guard.proxyBaseUrl,
      pid: runtime.pid,
    })
  } catch (error) {
    await guard.close()
    throw error
  }
  const stop = async (exitCode) => {
    runtime.removeAllListeners('SIGINT')
    runtime.removeAllListeners('SIGTERM')
    try {
      await guard.close()
      writeExclusiveJson(env.E2E_EGRESS_RECEIPT_FILE, guard.receipt())
      runtime.exitCode = exitCode
    } catch {
      runtime.exitCode = 1
    }
  }
  runtime.once('SIGINT', () => void stop(130))
  runtime.once('SIGTERM', () => void stop(143))
}
