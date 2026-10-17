/** Company SSO contract (plan 9.7, 10.10, S30; H-08). */

export type SsoProtocol = 'oidc' | 'ldap'
export type SsoMode = 'off' | 'optional' | 'required'
export type SsoConnectionStatus = 'draft' | 'tested' | 'active' | 'disabled'
export type SsoRole = 'member' | 'builder' | 'admin' | 'auditor'

export interface SsoLoginOption {
  id: string
  protocol: SsoProtocol
  name: string
  start_url: string | null
}

export interface SsoDiscovery {
  method: 'password' | 'sso'
  sso_required: boolean
  connections: SsoLoginOption[]
  login_csrf: string
}

export interface TenantDomain {
  id: string
  domain: string
  status: 'pending' | 'verified' | 'disabled'
  txt_record: string
  verified_at: string | null
  created_at: string
}

export interface SsoConnection {
  id: string
  protocol: SsoProtocol
  status: SsoConnectionStatus
  issuer: string
  client_id: string | null
  has_secret: boolean
  scopes: string[]
  server_url: string | null
  start_tls: boolean
  bind_dn: string | null
  user_base_dn: string | null
  user_filter: string | null
  jit_enabled: boolean
  trust_email_claim: boolean
  default_role: SsoRole
  tested_at: string | null
  redirect_uri: string | null
}

export interface SsoTestResponse {
  authorize_url: string | null
  result: { ok: boolean; error: string | null; claims: Record<string, unknown> | null } | null
  connection: SsoConnection
}

export interface SsoPolicy {
  mode: SsoMode
  break_glass_user_ids: string[]
  password_members: number
}

export type OidcConnectionInput = {
  protocol: 'oidc'
  issuer: string
  client_id: string
  client_secret: string
  jit_enabled: boolean
  trust_email_claim: boolean
  default_role: SsoRole
}

export type LdapConnectionInput = {
  protocol: 'ldap'
  server_url: string
  start_tls: boolean
  bind_dn: string
  bind_password: string
  user_base_dn: string
  user_filter: string
  jit_enabled: boolean
  trust_email_claim: boolean
  default_role: SsoRole
}
