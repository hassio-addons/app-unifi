"""App-owned heap override; never rewrite vendor defaults or generated units."""

from pathlib import Path
import json
import re
import sys

MARKER = "# Home Assistant app-owned Network heap override\n"
ENV = Path("/data/unifi-ha/network.env")
DROPIN = Path("/etc/systemd/system/unifi.service.d/90-home-assistant-options.conf")
DEFAULTS = (Path("/etc/default/unifi"), Path("/var/lib/unifi/env-overrides"))
BUS = Path("/etc/dbus-1/system.d/90-home-assistant.conf")
BUS_MARKER = "<!-- Home Assistant app-owned private-container D-Bus compatibility -->"


def apply_private_bus(text):
    if not text.startswith(BUS_MARKER):
        raise ValueError("Invalid app-owned private-bus configuration")
    if BUS.exists():
        previous = BUS.read_text()
        if not previous.startswith(BUS_MARKER):
            raise RuntimeError("Refusing to replace non-app-owned bus configuration")
        if previous == text:
            return False
    BUS.parent.mkdir(parents=True, exist_ok=True)
    temporary = BUS.with_name(BUS.name + ".tmp")
    temporary.write_text(text)
    temporary.chmod(0o644)
    temporary.replace(BUS)
    return True


def apply(maximum, initial):
    for path in (ENV, DROPIN):
        if path.exists() and not path.read_text().startswith(MARKER):
            raise RuntimeError("Refusing to replace a non-app-owned options file")
    if not maximum and not initial:
        changed = ENV.exists() or DROPIN.exists()
        ENV.unlink(missing_ok=True)
        DROPIN.unlink(missing_ok=True)
        return changed
    if any(value and not value.isdigit() for value in (maximum, initial)):
        raise ValueError("Heap options must be nonnegative integer MB values")
    if maximum and (int(maximum) == 0 or (initial and int(initial) > int(maximum))):
        raise ValueError("Invalid heap maximum/initial size")
    base = None
    for source in DEFAULTS:
        if source.is_file():
            for line in source.read_text().splitlines():
                match = re.fullmatch(r'\s*UNIFI_JVM_OPTS\s*=\s*"([^"\n]*)"\s*', line)
                if match:
                    base = match.group(1)
                elif re.match(r"\s*UNIFI_JVM_OPTS\s*=", line):
                    raise RuntimeError(
                        "Unsupported JVM environment syntax; refusing to discard existing options"
                    )
    if base is None:
        raise RuntimeError("Cannot read the vendor's JVM defaults safely")
    for flag, value in (("Xmx", maximum), ("Xms", initial)):
        if value:
            base = re.sub(r"(?:^|\s)-" + flag + r"\S+", "", base)
            base += f" -{flag}{value}m"

    def bound(flag):
        matches = re.findall(r"(?:^|\s)-" + flag + r"(\d+)([kKmMgG]?)(?=\s|$)", base)
        if not matches:
            return None
        value, unit = matches[-1]
        return int(value) * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[unit.lower()]

    effective_maximum, effective_initial = bound("Xmx"), bound("Xms")
    if (
        effective_maximum is not None
        and effective_initial is not None
        and effective_initial > effective_maximum
    ):
        raise ValueError("Merged initial heap exceeds retained/effective maximum")
    escaped = base.strip().replace("\\", "\\\\").replace('"', '\\"')
    files = {
        ENV: MARKER + f'UNIFI_JVM_OPTS="{escaped}"\n',
        DROPIN: MARKER + "[Service]\nEnvironmentFile=/data/unifi-ha/network.env\n",
    }
    changed = False
    for path, text in files.items():
        if not path.exists() or path.read_text() != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(text)
            temporary.chmod(0o644)
            temporary.replace(path)
            changed = True
    return changed


if __name__ == "__main__":
    network = apply(*sys.argv[1:3])
    bus = apply_private_bus(sys.argv[3])
    print(json.dumps({"network_changed": network, "bus_changed": bus}))
