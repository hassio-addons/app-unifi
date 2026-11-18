FROM ghcr.io/hassio-addons/unifi:5.3.2@sha256:9b1b59e7fcbe4d3d016782a5f6120753cd8a20252270f99eb231c05e3f338495 AS legacy_runtime
COPY build/collect-legacy-mongo-runtime /collect-legacy-mongo-runtime
RUN bash /collect-legacy-mongo-runtime

FROM ubuntu:18.04@sha256:152dc042452c496007f07ca9127571cb9c29697f42acbfad72324b2bb2e43c98 AS upgrade_runtime
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# Ubuntu 18.04 is end-of-life, so these pins are final (Renovate skips them).
RUN \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates=20230311ubuntu0.18.04.1 \
        curl=7.58.0-2ubuntu3.24 \
        libcurl4=7.58.0-2ubuntu3.24 \
        libssl1.0.0=1.0.2n-1ubuntu5.13 \
        libssl1.1=1.1.1-1ubuntu2.1~18.04.23
COPY build/collect-mongo-upgrade-runtimes /collect-mongo-upgrade-runtimes
RUN bash /collect-mongo-upgrade-runtimes

FROM ubuntu:26.04@sha256:065aa8626841f16057b78b027b464cd59c4fac36c204c72dcbf48d66f5fa8a96

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN \
    apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates=20260816ubuntu0.26.04.2 \
        curl=8.18.0-1ubuntu2.8 \
        dbus=1.16.2-2ubuntu4 \
        dbus-user-session=1.16.2-2ubuntu4 \
        iproute2=6.19.0-1ubuntu1.1 \
        jq=1.8.1-4ubuntu2 \
        libnginx-mod-http-js=0.9.4-1build2 \
        nginx=1.28.3-2ubuntu1.13 \
        passt=0.0~git20260120.386b5f5-1 \
        podman=5.7.0+ds2-3build1 \
        procps=2:4.0.4-9ubuntu1 \
        python3=3.14.3-0ubuntu2 \
        slirp4netns=1.3.3-1 \
        socat=1.8.1.1-1ubuntu0.1 \
        systemd=259.9-0ubuntu0.1 \
        systemd-sysv=259.9-0ubuntu0.1 \
        uidmap=1:4.17.4-2ubuntu3 \
        xz-utils=5.8.3-1ubuntu0.1 \
    && groupadd --gid 1001 uosserver \
    && useradd --uid 1001 --gid 1001 --home-dir /home/uosserver \
        --no-create-home --shell /bin/bash uosserver \
    && groupadd --system unifi \
    && useradd --system --gid unifi --no-create-home \
        --home-dir /usr/lib/unifi --shell /usr/sbin/nologin unifi \
    && mkdir -p /var/lib/unifi-os-app /usr/lib/unifi/data \
    && ln -s /var/lib/unifi-os-app/uos/home /home/uosserver \
    && ln -s /var/lib/unifi-os-app/uos/state /var/lib/uosserver \
    && mkdir -p /opt/unifi-os \
    && case "$(dpkg --print-architecture)" in \
        arm64) \
            url="https://fw-download.ubnt.com/data/unifi-os-server/f730-linux-arm64-5.1.42-d2bca8bd-d6fc-4d20-99e5-b78235843c2f.42-arm64"; \
            checksum="8e29bd50cde8f781ace4b85599256ef62c02723bdc751044d4db90a087fb5207" ;; \
        amd64) \
            url="https://fw-download.ubnt.com/data/unifi-os-server/5172-linux-x64-5.1.42-12e9e3cf-8f8b-4e54-928c-76b80a10c8a4.42-x64"; \
            checksum="f6111e9396a42c74016f5dde9b01fbef486a6fe69efe137935e6e38b7c22f94d" ;; \
        *) echo "Unsupported architecture" >&2; exit 1 ;; \
    esac \
    && curl -fLsS --retry 2 "${url}" -o /opt/unifi-os/installer \
    && echo "${checksum}  /opt/unifi-os/installer" | sha256sum --check \
    && chmod 0755 /opt/unifi-os/installer \
    && echo "5.1.42" > /opt/unifi-os/version \
    && systemctl mask systemd-modules-load.service systemd-udevd.service \
        systemd-udevd-control.socket systemd-udevd-kernel.socket \
        systemd-tmpfiles-setup-dev.service systemd-tmpfiles-setup-dev-early.service tmp.mount \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Ubiquiti publishes no installer signature; the pinned SHA-256 values are the
# recorded fingerprints of the official downloads. A changed file fails the build.
COPY --from=legacy_runtime /legacy-runtime /opt/unifi-legacy-mongo
COPY --from=upgrade_runtime /upgrade-runtimes /opt/unifi-mongo
COPY rootfs /
RUN chmod 0755 /usr/local/bin/unifi-os-* /usr/lib/unifi/bin/mongod \
    && useradd --system --user-group --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin unifi-port \
    && systemctl mask nginx.service console-getty.service getty.target getty@.service \
        serial-getty@.service systemd-remount-fs.service systemd-pstore.service \
    && systemctl enable unifi-os-bootstrap.service unifi-os-log.service unifi.service \
        unifi-os-shutdown-checkpoint.service \
        unifi-os-setup-notification.service \
        unifi-os-alert-notification.service unifi-os-alerts.path \
        unifi-os-setup-status.timer \
        unifi-os-ingress.service \
        unifi-os-port-alias@8443.service unifi-os-port-alias@8843.service \
        unifi-os-port-alias@10001.service

ENV container=docker
STOPSIGNAL SIGRTMIN+3
ENTRYPOINT ["/usr/local/bin/unifi-os-entrypoint"]

HEALTHCHECK --start-period=45m --interval=30s --timeout=20s --retries=3 \
    CMD ["/usr/local/bin/unifi-os-health"]

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
