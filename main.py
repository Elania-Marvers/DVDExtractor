from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dvdapp.config import build_settings
from dvdapp.drive_scanner import DriveScanner
from dvdapp.job_manager import RipManager
from dvdapp.server import DVWebServer, DVDRequestHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Simple DVD extractor web interface")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8080, help="Bind port")
    parser.add_argument("--poll", type=float, default=2.0, help="Poll interval in seconds")
    args = parser.parse_args()

    settings = build_settings(args.host, args.port, args.poll)
    scanner = DriveScanner()
    jobs = RipManager(settings.storage_path)

    server = DVWebServer((settings.host, settings.port), DVDRequestHandler, settings, scanner, jobs)

    logging.info("Storage: %s", settings.storage_path)
    logging.info("Starting server on http://%s:%s", settings.host, settings.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    main()
