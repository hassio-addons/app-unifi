"""Report which pinned vendor literals the ingress rewrites rely on are present.

Runs inside the nested OS (python3 -) with the literals as arguments. It reads
the served portal bundles and prints only one JSON object of booleans, never
vendor content.
"""

import json
from pathlib import Path
import sys

ROOT = Path("/usr/share/unifi-core/app/node_modules/@ubnt/unifi-portal/dist")
MAX_BYTES = 64 * 1024 * 1024

literals = [argument.encode() for argument in sys.argv[1:]]
found = [False] * len(literals)
for path in sorted(ROOT.rglob("*.js")):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        body = path.read_bytes()
    except OSError:
        continue
    for index, literal in enumerate(literals):
        found[index] = found[index] or literal in body
print(json.dumps({"bundles": ROOT.is_dir(), "found": found}))
