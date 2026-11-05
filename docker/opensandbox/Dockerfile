# OpenSandbox server, docker runtime (docs/design-docs/skill-sandbox-docker.md).
FROM python:3.12-slim

RUN pip install --no-cache-dir "opensandbox-server==1.1.1"

COPY docker/opensandbox/sandbox.toml /etc/opensandbox/sandbox.toml
COPY docker/opensandbox/start.py /opt/opensandbox/start.py

EXPOSE 8090
CMD ["python", "/opt/opensandbox/start.py"]
