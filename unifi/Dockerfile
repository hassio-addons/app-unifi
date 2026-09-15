ARG BUILD_FROM=ghcr.io/hassio-addons/ubuntu-base:8.2.0
# hadolint ignore=DL3006
FROM ${BUILD_FROM}

# Set shell
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Setup base system
# UniFi 10.1+ requires Java 25 — not in Ubuntu repos, use Eclipse Temurin.
# Temurin is not version-pinned: Adoptium uses different build suffixes per
# architecture (e.g. +10-1 on amd64 vs +10-0 on arm64), making a single
# pinned version string impossible across all supported architectures.
# hadolint ignore=DL3008
RUN \
    apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        gpg=2.2.19-3ubuntu2.5 \
    \
    && curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public \
        | gpg --dearmor -o /usr/share/keyrings/adoptium.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb focal main" \
        > /etc/apt/sources.list.d/adoptium.list \
    && apt-get update \
    \
    && apt-get install -y --no-install-recommends \
        binutils=2.34-6ubuntu1.11 \
        libcap2=1:2.32-1ubuntu0.2 \
        logrotate=3.14.0-4ubuntu3 \
        mongodb-server=1:3.6.9+really3.6.8+90~g8e540c0b6d-0ubuntu5.3 \
        nginx-extras=1.18.0-0ubuntu1.7 \
    \
    && apt-get install -y --no-install-recommends \
        temurin-25-jre \
    \
    && curl -J -L -o /tmp/unifi.deb \
        "https://dl.ui.com/unifi/10.6.106/unifi_sysvinit_all.deb" \
    \
    # Verify the package against Ubiquiti's published SHA256 for this release
    # (community.ui.com release page for 10.6.101). The build fails loudly if the
    # download does not match, e.g. a tampered, MITM'd, or truncated artifact.
    && echo "b5ab809c2680b7ec22106ef19c7942f1fa343338c3eed1829303d95b2ce4fdd7  /tmp/unifi.deb" \
        | sha256sum --check \
    \
    && dpkg --install /tmp/unifi.deb \
    \
    # Patch libubnt_webrtc_jni.so: strip the DONT-FRAGMENT (0x001A) attribute from
    # TURN Allocate requests. Cloudflare's TURN relay rejects it with error 420
    # (Unknown Attribute), breaking remote access via unifi.ui.com on all
    # self-hosted controllers. The _all deb ships both arch libs, so we can patch
    # both here regardless of build architecture.
    # Expected MD5s are for the unmodified originals — build fails loudly if
    # Ubiquiti ships a new library so offsets can be re-derived before proceeding.
    && AARCH64_LIB="/usr/lib/unifi/lib/native/Linux/aarch64/libubnt_webrtc_jni.so" \
    && X86_64_LIB="/usr/lib/unifi/lib/native/Linux/x86_64/libubnt_webrtc_jni.so" \
    && echo "27e786235fae4a052bc808c3c13dfc19  ${AARCH64_LIB}" | md5sum --check \
    && echo "657963ca47b185baf4eef8d90b70755b  ${X86_64_LIB}" | md5sum --check \
    # Helper: verify byte sequence at offset in library matches expected hex string.
    # On mismatch, emit a descriptive failure message and exit non-zero.
    && verify_bytes() { \
        local lib="$1" off="$2" count="$3" expect="$4" label="$5"; \
        local actual; \
        actual="$(dd if="${lib}" bs=1 skip=$((off)) count="${count}" 2>/dev/null | od -An -tx1 | tr -d ' \n')"; \
        if [ "${actual}" != "${expect}" ]; then \
            echo "FAIL: ${label} at $(printf '0x%X' "${off}"): expected ${expect}, got ${actual}" >&2; \
            return 1; \
        fi; \
    } \
    # Sanity-check target bytes before patching. Each offset must point to the
    # opcode for "mov type, #0x1a" that precedes AppendFieldEmpty.
    # aarch64: full 4-byte instruction 0x52800342 (mov w2, #0x1a), little-endian
    # x86_64:  5-byte instruction 0xba 0x1a 0x00 0x00 0x00 (mov edx, 0x1a)
    && verify_bytes "${AARCH64_LIB}" $((0x167214)) 4 "42038052" "aarch64 pre-patch site 1" \
    && verify_bytes "${AARCH64_LIB}" $((0x167D20)) 4 "42038052" "aarch64 pre-patch site 2" \
    && verify_bytes "${X86_64_LIB}"  $((0x114CB9)) 5 "ba1a000000" "x86_64 pre-patch site 1" \
    && verify_bytes "${X86_64_LIB}"  $((0x1157D7)) 5 "ba1a000000" "x86_64 pre-patch site 2" \
    # Apply the patches
    && printf '\x1f\x20\x03\xd5' | dd of="${AARCH64_LIB}" bs=1 seek=$((0x167214)) count=4 conv=notrunc 2>/dev/null \
    && printf '\x1f\x20\x03\xd5' | dd of="${AARCH64_LIB}" bs=1 seek=$((0x167D20)) count=4 conv=notrunc 2>/dev/null \
    && printf '\x90\x90\x90\x90\x90' | dd of="${X86_64_LIB}" bs=1 seek=$((0x114CB9)) count=5 conv=notrunc 2>/dev/null \
    && printf '\x90\x90\x90\x90\x90' | dd of="${X86_64_LIB}" bs=1 seek=$((0x1157D7)) count=5 conv=notrunc 2>/dev/null \
    # Verify patches actually landed
    && verify_bytes "${AARCH64_LIB}" $((0x167214)) 4 "1f2003d5" "aarch64 post-patch site 1" \
    && verify_bytes "${AARCH64_LIB}" $((0x167D20)) 4 "1f2003d5" "aarch64 post-patch site 2" \
    && verify_bytes "${X86_64_LIB}"  $((0x114CB9)) 5 "9090909090" "x86_64 post-patch site 1" \
    && verify_bytes "${X86_64_LIB}"  $((0x1157D7)) 5 "9090909090" "x86_64 post-patch site 2" \
    \
    # Patch base.js: fix a latent bug in UniFi's fetch-response adapter that
    # crashes the Angular login route in the mobile companion app (the inline
    # ingress UI shows a "400" page while desktop works). The adapter builds a
    # header map with:
    #   Array.from(resp.headers).reduce(function(e,t){return(e[t[0]]=t[1])&&e},{})
    # The "&&e" returns the accumulator only when the header value is truthy. An
    # empty-valued response header collapses the accumulator to "", so the next
    # header assignment writes to a string primitive, which strict mode rejects
    # with "Attempted to assign to readonly property" in any compliant JS engine.
    # That fails the route resolve and lands the SPA on /manage/fatal ("400").
    # Reproduced and patch-verified on iOS WebKit; desktop never hit it because
    # the responses it saw had no empty-valued header in a non-final position.
    # The bug is engine-agnostic, so this also covers the Android companion app's
    # WebView if it receives the same response. Switching "&&" to a comma makes
    # the reduce always return the accumulator object regardless of header values.
    # The bundle filename carries a per-release hash but this callback is a
    # content literal that survives renames; the count check fails the build
    # loudly if UniFi ever changes the code so the patch can be re-derived.
    && BASE_JS="$(ls /usr/lib/unifi/webapps/ROOT/app-unifi/angular/*/js/base.js)" \
    && BEFORE="$(grep -cF '(e[t[0]]=t[1])&&e' "${BASE_JS}" || true)" \
    && if [ "${BEFORE}" != "1" ]; then echo "FAIL: base.js header-map reduce: expected 1 occurrence, found ${BEFORE} (UniFi bundle changed; re-derive patch)" >&2; exit 1; fi \
    && sed -i 's/(e\[t\[0]]=t\[1])&&e/(e[t[0]]=t[1]),e/' "${BASE_JS}" \
    && AFTER_BUGGY="$(grep -cF '(e[t[0]]=t[1])&&e' "${BASE_JS}" || true)" \
    && AFTER_FIXED="$(grep -cF '(e[t[0]]=t[1]),e' "${BASE_JS}" || true)" \
    && if [ "${AFTER_BUGGY}" != "0" ] || [ "${AFTER_FIXED}" != "1" ]; then echo "FAIL: base.js header-map reduce patch did not land (buggy=${AFTER_BUGGY} fixed=${AFTER_FIXED})" >&2; exit 1; fi \
    && echo "OK: patched base.js header-map reduce in ${BASE_JS}" \
    \
    && apt-get clean \
    && rm -fr \
        /tmp/* \
        /var/cache/* \
        /var/lib/apt/lists/* \
        /var/log/*.log \
        /var/log/apt

# Copy root filesystem
COPY rootfs /

# Health check
HEALTHCHECK --start-period=5m \
    CMD curl --insecure --fail https://localhost:8443 || exit 1

# Build arguments
ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION
ARG BUILD_NAME
ARG BUILD_REF
ARG BUILD_REPOSITORY
ARG BUILD_VERSION

# Labels
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    maintainer="Franck Nijhof <opensource@frenck.dev>" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.vendor="Home Assistant Community Apps" \
    org.opencontainers.image.authors="Franck Nijhof <opensource@frenck.dev>" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.url="https://frenck.dev/home-assistant-apps" \
    org.opencontainers.image.source="https://github.com/${BUILD_REPOSITORY}" \
    org.opencontainers.image.documentation="https://github.com/${BUILD_REPOSITORY}/blob/main/README.md" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.revision=${BUILD_REF} \
    org.opencontainers.image.version=${BUILD_VERSION}
