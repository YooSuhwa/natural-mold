"""Company SSO (plan 10.10, S30; H-08): OIDC and LDAP/AD connections per company.

Domain verification (DNS TXT), connection test/activation, SSO-required with
break-glass administrators, JIT provisioning with IdP group → role mapping and
email-first login routing. Successful logins mint the existing JWT cookies.
"""
