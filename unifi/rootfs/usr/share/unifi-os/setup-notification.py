"""Status-only setup notice/dismissal, never migration orchestration or a receipt."""

import json
import math
import os
from pathlib import Path
import re
import ssl
import tempfile
import time
import urllib.error
import urllib.request

SUPERVISOR = "http://supervisor"
VENDOR = "https://127.0.0.1:11443"
RETRY_SECONDS = 15
WAIT_SECONDS = 900
POLL_SECONDS = 30
STATUS = Path("/run/unifi-os-setup-status/status.json")
STATUS_MAX_AGE = 90
# Persistent record that the vendor explicitly reported isSetup: true (see
# setup-status.py). Sampling stops once it exists, so it settles the status.
SETUP_COMPLETE = Path("/var/lib/unifi-os-app/uos/setup-status/complete")
SECURE_PORT = Path("/run/unifi-os-console-port/secure-port.json")
PORT_ATTEMPTS = 20
PORT_RETRY_SECONDS = 3


def request_json(url, *, token=None, payload=None, vendor=False):
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        headers=headers,
        data=json.dumps(payload).encode() if payload is not None else None,
    )
    # No proxy discovery/redirects: never forward the credential to another host.
    handlers = [urllib.request.ProxyHandler({}), NoRedirect()]
    if vendor:
        assert url.startswith(VENDOR + "/")
        handlers.append(
            urllib.request.HTTPSHandler(context=ssl._create_unverified_context())
        )
    opener = urllib.request.build_opener(*handlers)
    with opener.open(request, timeout=10) as response:
        body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ValueError("Oversized status response")
        return json.loads(body) if body else None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def setup_needed(system):
    # After setup the public API can omit isSetup. Omission is UNKNOWN, not a
    # migration completion signal, so it must never create/clear a notification.
    if not isinstance(system, dict) or type(system.get("isSetup")) is not bool:
        return None
    return not system["isSetup"]


def native_setup_needed():
    try:
        with STATUS.open("rb") as stream:
            body = stream.read(4097)
        if len(body) > 4096:
            return None
        status = json.loads(body)
        sampled = status.get("sampled_at")
        if type(sampled) not in (int, float) or not math.isfinite(sampled):
            return None
        age = time.monotonic() - sampled
        if not 0 <= age <= STATUS_MAX_AGE:
            return None
        return setup_needed(status)
    except (OSError, ValueError, AttributeError, OverflowError):
        return None


def console_port(info):
    # A mapping the user changed shows intent (and what their firewall allows),
    # so prefer customized bindings, the vendor port first; then the defaults.
    # With both explicitly off, use the native port (only reachable if exposed).
    # No network map at all is not an answer: return None so nothing is guessed.
    network = info.get("network")
    if not isinstance(network, dict):
        return None
    candidates = {}
    for key, default in (("11443/tcp", 11443), ("8443/tcp", 8443)):
        port = network.get(key)
        if type(port) is int and 1 <= port <= 65535:
            candidates[key] = (port, port != default)
    for customized in (True, False):
        for key in ("11443/tcp", "8443/tcp"):
            if key in candidates and candidates[key][1] is customized:
                return candidates[key][0]
    return 11443


def publish_console_port(info):
    # Public routing metadata only. No token, hostname, options or API response
    # is published to nginx. The DynamicUser-owned runtime directory is transient.
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=".secure-port-", dir=SECURE_PORT.parent
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"port": console_port(info)}, stream)
        temporary.chmod(0o644)
        temporary.replace(SECURE_PORT)
        return True
    except OSError:
        # A missing optional runtime directory must not break setup notices.
        return False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def refresh_console_port(token):
    # Routing must be available even when vendor setup status is unknown. Keep
    # this independent of notification create/dismiss decisions.
    info = request_json(SUPERVISOR + "/addons/self/info", token=token)
    if (
        not isinstance(info, dict)
        or info.get("result") != "ok"
        or not isinstance(info.get("data"), dict)
    ):
        raise ValueError("Invalid self-info response")
    if console_port(info["data"]) is None:
        # Leave any previous publication alone; the unit retries on failure.
        raise ValueError("Self-info response has no network map")
    return publish_console_port(info["data"])


def notification(slug, legacy, *, ingress=False):
    if not re.fullmatch(r"[a-z0-9_-]+", slug):
        raise ValueError("Invalid app identity")
    message = (
        "UniFi OS Server needs its initial setup completed. "
        "Open **Settings → Apps → UniFi OS Server → Open Web UI** and follow "
        "Ubiquiti's setup wizard. "
    )
    # The generic HA app launcher works even when Show in sidebar is disabled.
    # A bare /<slug> requires a registered sidebar panel; ingress_url has a token.
    if ingress is True:
        message += f"[Open UniFi Web UI](/app/{slug}). "
    else:
        message += (
            f"[Open UniFi app](/config/app/{slug}/info) and select **Open Web UI**. "
        )
    if legacy is True:
        message += (
            "Your retained UniFi Network data was detected; select the vendor's "
            "migration/import option when offered. Nothing has been imported automatically. "
        )
    message += (
        "Use Ubiquiti sign-in for the built-in migration path. Local-account "
        "legacy import is not supported: export a Network .unf backup before "
        "upgrading, complete UniFi OS setup, then restore that file in Network's "
        "Settings → Control Plane → Backups. See "
        "[Ubiquiti's backup migration guide](https://help.ui.com/hc/en-us/articles/360008976393-Backups-and-Migration-in-UniFi). "
        "Do not enable "
        "device connections until you have verified the imported configuration. "
        "This notification clears when the vendor explicitly confirms setup is complete."
    )
    return {
        "notification_id": "unifi_os_setup_" + slug,
        "title": (
            "UniFi Network migration setup needed"
            if legacy is True
            else "UniFi OS setup needed"
        ),
        "message": message,
    }


def report(token, *, notified=False):
    if SETUP_COMPLETE.is_file():
        system = {"isSetup": True}
    else:
        try:
            system = request_json(VENDOR + "/api/system", vendor=True)
        except (OSError, ValueError):
            system = None
    needed = setup_needed(system)
    if needed is None:
        needed = native_setup_needed()
    if needed is None:
        return "unknown"
    if needed and notified:
        return "notified"
    info = request_json(SUPERVISOR + "/addons/self/info", token=token)
    if not isinstance(info, dict) or info.get("result") != "ok":
        raise ValueError("Invalid self-info response")
    slug = info["data"]["slug"]
    if not re.fullmatch(r"[a-z0-9_-]+", slug):
        raise ValueError("Invalid app identity")
    if not needed:
        request_json(
            SUPERVISOR + "/core/api/services/persistent_notification/dismiss",
            token=token,
            payload={"notification_id": "unifi_os_setup_" + slug},
        )
        return "configured"
    try:
        legacy = request_json(
            VENDOR + "/api/backup/setup/has-legacy-net", vendor=True
        ).get("hasLegacyNet")
    except (OSError, ValueError, AttributeError):
        legacy = None
    request_json(
        SUPERVISOR + "/core/api/services/persistent_notification/create",
        token=token,
        payload=notification(slug, legacy, ingress=info["data"].get("ingress") is True),
    )
    return "notified"


def read_credential():
    credential = Path(os.environ["CREDENTIALS_DIRECTORY"]) / "ha-api-token"
    return credential.read_text().strip()


def publish_main():
    # Runs early, before the ingress bridge and independently of the (possibly
    # long) vendor bootstrap, so the configured console port is already known.
    token = read_credential()
    if not token:
        print("Console port not published: no API credential")
        return 1
    for attempt in range(PORT_ATTEMPTS):
        try:
            if refresh_console_port(token):
                print("Configured console port published for the ingress bridge")
                return 0
        except (OSError, ValueError, KeyError, TypeError):
            # Never print request headers, tokens or API bodies.
            pass
        if attempt + 1 < PORT_ATTEMPTS:
            time.sleep(PORT_RETRY_SECONDS)
    print("Console port not yet available from Supervisor; will retry", flush=True)
    return 1


def main():
    token = read_credential()
    if not token:
        print("Setup notification skipped: no Core API credential")
        return 0
    deadline = time.monotonic() + WAIT_SECONDS
    notified = False
    while True:
        try:
            result = report(token, notified=notified)
        except (OSError, ValueError, KeyError, TypeError):
            # Never print request headers, tokens, API bodies or user information.
            time.sleep(RETRY_SECONDS)
            result = "unknown"
        if result == "configured":
            print(
                "Vendor setup complete; this app's setup notification dismissed",
                flush=True,
            )
            return 0
        elif result == "notified":
            if not notified:
                print(
                    "UniFi setup-needed notification delivered to Home Assistant",
                    flush=True,
                )
            notified = True
            deadline = time.monotonic() + WAIT_SECONDS
        elif time.monotonic() >= deadline:
            print(
                "Setup notification unavailable: no explicit vendor/API status; app startup is unaffected",
                flush=True,
            )
            return 1
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["--publish-console-port"]:
        raise SystemExit(publish_main())
    raise SystemExit(main())
