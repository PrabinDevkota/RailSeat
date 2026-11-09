"""Replica process. Read-only API plus a puller that applies the primary's change log."""

import sys

from app.servers.node import main

if __name__ == "__main__":
    if "--role" not in sys.argv:
        sys.argv[1:1] = ["--role", "replica"]
    main()
