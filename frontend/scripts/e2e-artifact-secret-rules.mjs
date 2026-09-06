export const E2E_SECRET_SCAN_RULE_IDS = Object.freeze([
  'configured_exact_secret',
  'private_key',
  'bearer_token',
  'sensitive_assignment',
  'credential_dsn',
  'jwt',
  'credential_query',
  'structured_sensitive_value',
  'structured_cookie_value',
  'representation_bounds',
])

export class SecretScanFailure extends Error {
  constructor(ruleId) {
    super('E2E artifact secret scan failed.')
    this.name = 'SecretScanFailure'
    this.ruleId = ruleId
  }
}
