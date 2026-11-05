# syntax=docker/dockerfile:1
# Korean PII model service (docker/pii-detector/server.py). The checkpoint is not in the image:
# pii-model-fetch downloads the pinned files into a volume (or mount your own copy there).
FROM python:3.12-slim

# Versions measured in docs/design-docs/korean-pii-detector-benchmark.md. torch comes from the
# CPU index: the PyPI wheels pull about 4 GB of CUDA libraries on both amd64 and arm64.
# The optional extra_ca build secret is a CA bundle for networks that inspect TLS; it is used
# for this install only and not kept in the image.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then \
      cat /etc/ssl/certs/ca-certificates.crt /run/secrets/extra_ca > /tmp/ca.pem \
      && export PIP_CERT=/tmp/ca.pem; \
    fi \
 && pip install --no-cache-dir --index-url "$TORCH_INDEX_URL" "torch==2.15.0" \
 && pip install --no-cache-dir "schift-ko-pii==0.6.0" "transformers==5.22.1" \
      "huggingface-hub==2.4.0" "safetensors==0.8.0" "tokenizers==0.23.3" \
 && rm -f /tmp/ca.pem

# /models is owned by the service user so a new named volume mounted there is writable.
RUN useradd --uid 1000 --create-home detector \
 && mkdir -p /models/schift-ko-pii-v7 && chown -R detector /models
COPY docker/pii-detector/model_files.py docker/pii-detector/fetch.py docker/pii-detector/server.py /opt/pii-detector/
WORKDIR /opt/pii-detector
USER detector

EXPOSE 8095
CMD ["python", "/opt/pii-detector/server.py"]
