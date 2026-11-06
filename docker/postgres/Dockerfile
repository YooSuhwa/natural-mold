# syntax=docker/dockerfile:1
# Moldy's PostgreSQL: the same postgres:16-alpine the stack has always used, plus the pgvector
# extension (feature plan v2 순번 5, 공통 기반). Staying on the Alpine base keeps existing data
# volumes valid: a Debian-based image (pgvector/pgvector) would switch the C library and with
# it text collation, which can silently reorder existing text indexes.
FROM postgres:16-alpine

# with_llvm=no: no JIT bitcode, so the build needs no clang/LLVM matching the base image's.
# OPTFLAGS="": portable code, not tuned to the build machine's CPU.
ARG PGVECTOR_VERSION=0.8.1
ARG PGVECTOR_SHA256=a9094dfb85ccdde3cbb295f1086d4c71a20db1d26bf1d6c39f07a7d164033eb4
RUN --mount=type=secret,id=extra_ca,required=false \
    set -eu \
 && apk add --no-cache --virtual .build-deps build-base ca-certificates curl \
 && if [ -s /run/secrets/extra_ca ]; then cat /run/secrets/extra_ca >> /etc/ssl/certs/ca-certificates.crt; fi \
 && curl -fsSL -o /tmp/pgvector.tar.gz \
      "https://github.com/pgvector/pgvector/archive/refs/tags/v${PGVECTOR_VERSION}.tar.gz" \
 && echo "${PGVECTOR_SHA256}  /tmp/pgvector.tar.gz" | sha256sum -c - \
 && mkdir /tmp/pgvector && tar -xzf /tmp/pgvector.tar.gz -C /tmp/pgvector --strip-components=1 \
 && make -C /tmp/pgvector OPTFLAGS="" with_llvm=no \
 && make -C /tmp/pgvector install with_llvm=no \
 && rm -rf /tmp/pgvector /tmp/pgvector.tar.gz \
 && apk del .build-deps
