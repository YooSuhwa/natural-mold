"""Prepare the network, then run the OpenSandbox server (docs/design-docs/skill-sandbox-docker.md).

1. Join Docker's default bridge, where the server starts sandboxes: it proxies the
   backend's execd calls to their addresses there, and Compose cannot attach a
   service to that network. The Docker socket the server needs already allows it.
2. Fill ``publish_host`` / ``host_ip`` in sandbox.toml. Sandbox ports are published
   on the Docker host and this server, being in a container, reaches them there:
   on Docker Desktop through host.docker.internal to 127.0.0.1, on Linux at the
   bridge gateway. Neither is reachable from outside the host. Override with
   OPENSANDBOX_PUBLISH_HOST and OPENSANDBOX_HOST_IP.
3. Pull the execd and egress images now, so the first skill call does not wait
   for a download (the egress image is about 560 MB).
"""

import os
import socket
import tomllib
from pathlib import Path

import docker

TEMPLATE = Path("/etc/opensandbox/sandbox.toml")
CONFIG = Path("/var/lib/opensandbox/sandbox.toml")

client = docker.from_env()
container = client.containers.get(socket.gethostname())
if "bridge" not in container.attrs["NetworkSettings"]["Networks"]:
    client.networks.get("bridge").connect(container)

if client.info().get("OperatingSystem") == "Docker Desktop":
    publish_host, host_ip = "127.0.0.1", "host.docker.internal"
else:
    gateway = client.networks.get("bridge").attrs["IPAM"]["Config"][0]["Gateway"]
    publish_host = host_ip = gateway
publish_host = os.environ.get("OPENSANDBOX_PUBLISH_HOST") or publish_host
host_ip = os.environ.get("OPENSANDBOX_HOST_IP") or host_ip

config = tomllib.loads(TEMPLATE.read_text())
for image in (config["runtime"]["execd_image"], config["egress"]["image"]):
    try:
        client.images.get(image)
    except docker.errors.ImageNotFound:
        print(f"opensandbox: pulling {image}", flush=True)
        client.images.pull(image)

CONFIG.write_text(
    TEMPLATE.read_text().replace("@PUBLISH_HOST@", publish_host).replace("@HOST_IP@", host_ip)
)
os.environ["SANDBOX_CONFIG_PATH"] = str(CONFIG)
print(f"opensandbox: sandbox ports on {publish_host}, reached at {host_ip}", flush=True)
os.execvp("opensandbox-server", ["opensandbox-server"])  # noqa: S606, S607 - the image's server
