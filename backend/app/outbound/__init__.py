"""Outbound HTTP for credentialed and agent-driven calls (plan 11.9; F-11).

* ``client.outbound_http_client`` — the one constructor for ``httpx`` clients: the
  shared trust store, timeouts, optional https-only, SSRF guard with DNS pinning and
  same-host redirects.
* ``destination`` — where a resolved credential's values may be sent (D18).
* ``hosts`` — host pattern matching and validation.
"""
