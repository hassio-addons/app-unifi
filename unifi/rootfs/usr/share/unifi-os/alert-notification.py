"""Deliver app alerts from /run/unifi-os-alerts as Home Assistant notifications.

Alert files are written only by root-owned app components. Each file name is a
fixed alert ID; the first line is the title and the rest is the message. This
reporter runs without privileges and never prints tokens or API bodies.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time

ALERTS = Path("/run/unifi-os-alerts")
# Per-boot record of what was delivered, so a later alert does not re-show
# notifications the user already dismissed (DynamicUser runtime directory).
STATE = Path("/run/unifi-os-alert-notification/delivered.json")
STARTUP_COMPLETE = "startup-complete"
# Start failures are withdrawn once a later start succeeds.
STARTUP_ALERTS = (
    "startup_refused",
    "checkpoint_restore_failed",
    "os_version_drift",
    "os_downgrade",
    "partial_installation",
    "vendor_state_untrusted",
    "vendor_binary_untrusted",
    "legacy_layout_failed",
    "mongo_preparation_failed",
    "vendor_install_failed",
    "vendor_start_failed",
    "bootstrap_failed",
)
ALERT_ID = re.compile(r"[a-z][a-z0-9_]{0,47}")
MAX_ALERT_BYTES = 8192
ATTEMPTS = 3
RETRY_SECONDS = 5
HELPERS = Path("/usr/share/unifi-os/setup-notification.py")


def helpers():
    spec = importlib.util.spec_from_file_location("setup_notification", HELPERS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_alerts(directory):
    alerts = {}
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return alerts
    for path in entries:
        if not ALERT_ID.fullmatch(path.name) or path.is_symlink() or not path.is_file():
            continue
        try:
            with path.open("rb") as stream:
                body = stream.read(MAX_ALERT_BYTES + 1)
            if len(body) > MAX_ALERT_BYTES:
                continue
            title, _, message = body.decode("utf-8").partition("\n")
        except (OSError, UnicodeDecodeError):
            continue
        if title.strip() and message.strip():
            alerts[path.name] = (title.strip(), message.strip())
    return alerts


def fingerprint(alert):
    return hashlib.sha256(json.dumps(alert).encode()).hexdigest()


def load_state(path):
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(path, state):
    if path is None:
        return
    try:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(state))
        temporary.replace(path)
    except OSError:
        pass


def notification_id(alert_id, slug):
    return f"unifi_os_{alert_id}_{slug}"


def app_slug(module, token):
    info = module.request_json(module.SUPERVISOR + "/addons/self/info", token=token)
    if not isinstance(info, dict) or info.get("result") != "ok":
        raise ValueError("Invalid self-info response")
    slug = info["data"]["slug"]
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9_-]+", slug):
        raise ValueError("Invalid app identity")
    return slug


def deliver_once(module, token, directory, state):
    """Deliver new/changed alerts; return the number of notifications sent."""
    alerts = read_alerts(directory)
    pending = {
        key: value
        for key, value in alerts.items()
        if state.get(key) != fingerprint(value)
    }
    complete = (directory / STARTUP_COMPLETE).is_file()
    dismiss = [
        key
        for key in STARTUP_ALERTS
        if complete and key not in alerts and state.get(key) != "dismissed"
    ]
    if not pending and not dismiss:
        return 0
    slug = app_slug(module, token)
    sent = 0
    for key, (title, message) in pending.items():
        module.request_json(
            module.SUPERVISOR + "/core/api/services/persistent_notification/create",
            token=token,
            payload={
                "notification_id": notification_id(key, slug),
                "title": "UniFi OS Server: " + title,
                "message": message,
            },
        )
        state[key] = fingerprint((title, message))
        sent += 1
    for key in dismiss:
        module.request_json(
            module.SUPERVISOR + "/core/api/services/persistent_notification/dismiss",
            token=token,
            payload={"notification_id": notification_id(key, slug)},
        )
        state[key] = "dismissed"
        sent += 1
    return sent


def deliver(module, token, directory, state_path):
    # Re-scan until nothing new arrives: an alert written while this ran may
    # not trigger another start of the path-activated unit.
    state = load_state(state_path) if state_path else {}
    for _ in range(10):
        for attempt in range(ATTEMPTS):
            try:
                sent = deliver_once(module, token, directory, state)
                save_state(state_path, state)
                if sent == 0:
                    return True
                break
            except (OSError, ValueError, KeyError, TypeError):
                # Never print request headers, tokens or API bodies.
                if attempt + 1 == ATTEMPTS:
                    return False
                time.sleep(RETRY_SECONDS)
    return True


def main(argv):
    if argv == ["--token-stdin"]:
        # Direct delivery by the entrypoint before systemd runs: no state.
        token = sys.stdin.read(4096).strip()
        state_path = None
    elif not argv:
        credential = Path(os.environ["CREDENTIALS_DIRECTORY"]) / "ha-api-token"
        token = credential.read_text().strip()
        state_path = STATE
    else:
        print("Usage: alert-notification.py [--token-stdin]", file=sys.stderr)
        return 2
    if not token:
        print("App alerts not sent to Home Assistant: no Core API credential")
        return 0
    if not deliver(helpers(), token, ALERTS, state_path):
        print("App alerts could not be delivered to Home Assistant", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
