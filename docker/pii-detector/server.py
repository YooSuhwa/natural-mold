# pyright: reportMissingImports=false
# Runs in the pii-detector image (torch, schift-ko-pii), not the backend venv.
"""Korean PII model service: schift-ko-pii 0.6.0 with the pinned v7 checkpoint.

The backend's ``kr_model`` pack calls POST /detect (guardrail-minimum-plan.md §2). It runs in
its own container so torch and the checkpoint's trust_remote_code stay out of the backend.
Only the files pinned in model_files.py are loaded, from PII_MODEL_DIR, with the hub offline.

POST /detect  {"texts": ["..."]} -> {"model": "...", "results": [[{entity, start, end, score}]]}
GET  /healthz 200 when the model is loaded

PII_DETECTOR_API_KEY, when set, is required as a bearer token. Compose leaves it unset: the
service publishes no host port and the skill sandboxes cannot reach the Compose network.
Without the pinned checkpoint the service stays up and answers 503 ``model_missing``, so the
backend uses its rules pack and the rest of the stack starts normally.
"""

from __future__ import annotations

import hmac
import importlib
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import torch  # noqa: E402
from model_files import REPO_ID, REVISION, mismatched  # noqa: E402

MODEL_DIR = Path(os.environ.get("PII_MODEL_DIR", "/models/schift-ko-pii-v7"))
API_KEY = os.environ.get("PII_DETECTOR_API_KEY", "")
PORT = int(os.environ.get("PII_DETECTOR_PORT", "8095"))
# Concurrent inferences; each uses one torch thread (more threads contend and slow down).
WORKERS = int(os.environ.get("PII_DETECTOR_WORKERS", "2"))
MAX_BODY_BYTES = 1 << 20
MAX_TEXTS = 64
QUEUE_TIMEOUT_SECONDS = 10.0
MODEL_NAME = f"{REPO_ID}@{REVISION[:8]}"

# schift labels -> the ko-pii label names the backend packs use (app/guardrails/detectors.py).
LABELS = {
    "private_person": "PERSON",
    "private_email": "EMAIL",
    "private_phone": "PHONE",
    "resident_id": "RRN",
    "account_number": "ACCOUNT",
    "private_address": "ADDRESS",
    "private_url": "URL",
    "ip": "IP",
    "card": "CARD",
    "passport": "PASSPORT",
    "driver_license": "DRIVER_LICENSE",
    "medical_insurance": "MEDICAL_INSURANCE",
    "vehicle": "VEHICLE",
    "birth_date": "DT_BIRTH",
    "corporate_registration": "CORP_REG",
}
IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
# The module, not the package's re-exported detect() function.
schift = importlib.import_module("schift_ko_pii.detect")

_slots = threading.BoundedSemaphore(WORKERS)
_ready = threading.Event()


def _entity(result: dict) -> str:
    label = str(result["label"])
    entity = LABELS.get(label, label.upper())
    # The URL path also claims bare IPv4 addresses.
    if entity == "URL" and IPV4.match(str(result.get("text", ""))):
        return "IP"
    return entity


def detect(text: str) -> list[dict]:
    return [
        {
            "entity": _entity(r),
            "start": int(r["start"]),
            "end": int(r["end"]),
            "score": round(float(r["score"]), 4),
        }
        for r in schift.detect(text)
    ]


def load() -> None:
    bad = mismatched(MODEL_DIR)
    if bad:
        print(f"pii-detector: {MODEL_DIR} lacks the pinned checkpoint: {bad}", flush=True)
        return
    torch.set_num_threads(1)
    schift.set_model_id(str(MODEL_DIR))
    # The package fetches the manifest and weights through the hub; read the pinned files.
    setattr(schift, "_hf_download", lambda filename: str(MODEL_DIR / filename))  # noqa: B010
    detect("워밍업 홍길동 010-1234-5678")
    _ready.set()


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/healthz":
            self._send(404, {"error": "not_found"})
        elif not _ready.is_set():
            self._send(503, {"error": "model_missing"})
        else:
            self._send(200, {"model": MODEL_NAME})

    def do_POST(self) -> None:
        if self.path != "/detect":
            self._send(404, {"error": "not_found"})
            return
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if API_KEY and not hmac.compare_digest(supplied.encode(), API_KEY.encode()):
            self._send(401, {"error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send(413, {"error": "body_size"})
            return
        try:
            texts = json.loads(self.rfile.read(length))["texts"]
        except (ValueError, KeyError, TypeError):
            texts = None
        if (
            not isinstance(texts, list)
            or len(texts) > MAX_TEXTS
            or not all(isinstance(t, str) for t in texts)
        ):
            self._send(400, {"error": "invalid_texts"})
            return
        if not _ready.is_set():
            self._send(503, {"error": "model_missing"})
            return
        if not _slots.acquire(timeout=QUEUE_TIMEOUT_SECONDS):
            self._send(503, {"error": "busy"})
            return
        try:
            results = [detect(text) for text in texts]
        finally:
            _slots.release()
        self._send(200, {"model": MODEL_NAME, "results": results})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # Request lines only; bodies (the text under check) are never logged.
        sys.stderr.write(f"pii-detector: {format % args}\n")


def main() -> None:
    load()
    model = MODEL_NAME if _ready.is_set() else "no model"
    print("pii-detector: serving", model, "on", PORT, flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104


if __name__ == "__main__":
    main()
