# syntax=docker/dockerfile:1
# Moldy's shared MCP runtime (moldy_mcp_runtime, plan §7.3). Stateless: replicas share
# nothing; each call carries a run token signed by the backend (MCP_RUNTIME_TOKEN_SECRET).
FROM python:3.12-slim

# The optional extra_ca build secret is a CA bundle for networks that inspect TLS; it is used
# for this install only and not kept in the image.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then \
      cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca > /tmp/ca.pem \
      && export PIP_CERT=/tmp/ca.pem; \
    fi \
 && pip install --no-cache-dir "fastmcp==4.1.5" "pyjwt==2.10.1" "uvicorn==0.38.0" \
      "httpx2==2.16.0" "cryptography==50.0.2" \
 && rm -f /tmp/ca.pem

RUN useradd --uid 1000 --create-home runtime
COPY mcp-runtime/moldy_mcp_runtime /opt/mcp-runtime/moldy_mcp_runtime
WORKDIR /opt/mcp-runtime
# Runs as the unprivileged user; in Compose the entrypoint first reads its one secret from
# the root-only generated file, then drops to this user (setpriv) before the server starts.
USER runtime

EXPOSE 8096
CMD ["python", "-m", "moldy_mcp_runtime"]
