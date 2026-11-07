# syntax=docker/dockerfile:1
# OpenSearch (Apache-2.0) with the Nori Korean analyzer (analysis-nori), for memory recall's
# Korean terms (memory-policy-plan 5.4-11). Moldy only calls `_analyze`: it keeps no index
# and no memory text here. Elasticsearch is not used (licence).
FROM opensearchproject/opensearch:2.19.1

# The optional extra_ca build secret is a CA bundle for networks that inspect TLS; a copy of
# the bundled JDK trust store gets it for the plugin download only and is not kept.
RUN --mount=type=secret,id=extra_ca,required=false,uid=1000 \
    set -e; \
    JAVA=/usr/share/opensearch/jdk; \
    if [ -s /run/secrets/extra_ca ]; then \
      cp "$JAVA/lib/security/cacerts" /tmp/trust; \
      awk 'BEGIN{n=0} /BEGIN CERTIFICATE/{n++} n{print > ("/tmp/ca-" n ".pem")}' /run/secrets/extra_ca; \
      for cert in /tmp/ca-*.pem; do \
        "$JAVA/bin/keytool" -importcert -noprompt -keystore /tmp/trust -storepass changeit \
          -alias "$(basename "$cert")" -file "$cert" >/dev/null; \
      done; \
      export OPENSEARCH_JAVA_OPTS="-Djavax.net.ssl.trustStore=/tmp/trust -Djavax.net.ssl.trustStorePassword=changeit"; \
    fi; \
    /usr/share/opensearch/bin/opensearch-plugin install --batch analysis-nori; \
    rm -f /tmp/trust /tmp/ca-*.pem
