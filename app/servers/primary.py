"""Primary process. Writes, the change log, and consistent reads."""

import os

from app.servers.node import main

if __name__ == "__main__":
    os.environ.setdefault("RAILSEAT_ROLE", "primary")
    os.environ.setdefault("PORT", "8000")
    os.environ.setdefault("NODE_URL", "http://127.0.0.1:8000")
    main()
