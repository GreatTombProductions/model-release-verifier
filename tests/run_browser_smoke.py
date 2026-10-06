#!/usr/bin/env python3
"""Run the Playwright smoke test.

Modes:
  default            stage frontend + data/generated/index.json in a temp dir and serve it
  SMOKE_SITE=<dir>   serve an assembled deploy directory as-is
  SMOKE_BASE=<url>   test a live URL directly (no local server)
"""
from __future__ import annotations

import functools
import http.server
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def _run_node(base: str) -> None:
    environment = os.environ.copy()
    environment["SMOKE_BASE"] = base
    subprocess.run(["node", str(PROJECT / "tests" / "browser_smoke.js")], check=True, timeout=180, env=environment)


def main() -> int:
    if os.environ.get("SMOKE_BASE"):
        _run_node(os.environ["SMOKE_BASE"])
        return 0
    with tempfile.TemporaryDirectory(prefix="mrv-smoke-") as temporary:
        if os.environ.get("SMOKE_SITE"):
            stage = Path(os.environ["SMOKE_SITE"])
        else:
            stage = Path(temporary)
            shutil.copytree(PROJECT / "frontend", stage, dirs_exist_ok=True)
            (stage / "data").mkdir()
            shutil.copy2(PROJECT / "data" / "generated" / "index.json", stage / "data" / "index.json")
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=stage)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _run_node(f"http://127.0.0.1:{server.server_port}/")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
