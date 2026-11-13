"""Back up and check the audit database.

    python -m app.backup backups/mulering-2026-11-13.db    copy the live database
    python -m app.backup --verify backups/some-copy.db     check a copy's audit chain

The copy is made with SQLite's online backup, so it is consistent even while
the application is writing. Verifying a restored copy proves the backup can be
read and that no audit entry was altered.
"""
from __future__ import annotations

import os
import sys

from . import config
from .store import Store


def _main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "--verify":
        if not os.path.exists(argv[1]):
            print(f"{argv[1]} does not exist")
            return 1
        result = Store(argv[1]).verify()
        print(f"audit chain {'intact' if result['ok'] else 'BROKEN at entry ' + str(result['broken_at'])}: "
              f"{result['entries']} entries")
        return 0 if result["ok"] else 1
    if len(argv) == 1 and not argv[0].startswith("-"):
        source = os.path.join(config.DATA_DIR, "mulering.db")
        if not os.path.exists(source):
            print(f"no database at {source}")
            return 1
        size = Store(source).backup(argv[0])
        print(f"wrote {argv[0]} ({size / 1024:.0f} KB)")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
