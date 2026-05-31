from __future__ import annotations

import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import unquote, urlparse

from .drive_scanner import DriveScanner
from .encryption import detect_encryption
from .job_manager import RipManager


INDEX_HTML = (Path(__file__).resolve().parents[1] / "static" / "index.html").resolve()
STATIC_ROOT = INDEX_HTML.parent


class DVDRequestHandler(BaseHTTPRequestHandler):
    scanner: DriveScanner = None  # type: ignore[assignment]
    jobs: RipManager = None  # type: ignore[assignment]
    poll_interval: float = 2.0

    def _set_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._set_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _respond_error(self, status: int, message: str, details: str | None = None) -> None:
        payload = {"error": message}
        if details:
            payload["details"] = details
        self._json(status, payload)

    def _text(self, status: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self._set_cors_headers()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_index(self) -> None:
        self._serve_static("/index.html")

    def _serve_static(self, request_path: str) -> None:
        requested = (request_path or "/").lstrip("/")
        if requested.startswith("static/"):
            requested = requested.removeprefix("static/")
        if requested in {"", "index.html"}:
            file_path = INDEX_HTML
        else:
            if ".." in requested.split("/"):
                self._respond_error(400, "invalid path")
                return
            file_path = (STATIC_ROOT / requested).resolve()
            static_root = STATIC_ROOT.resolve()
            if not str(file_path).startswith(f"{static_root}/"):
                self._respond_error(400, "invalid path")
                return

        if not file_path.exists():
            self._text(404, "file not found")
            return

        try:
            data = file_path.read_bytes()
        except Exception as exc:
            logging.exception("failed reading static file %s", file_path)
            self._respond_error(500, "cannot read static file", str(exc))
            return

        suffix = file_path.suffix.lower()
        content_type, _ = guess_type(str(file_path), strict=False)
        if suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif suffix == ".html":
            content_type = "text/html; charset=utf-8"

        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors_headers()
        self.end_headers()

    def _read_json(self) -> tuple[dict | None, str | None]:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            return None, "invalid content-length"

        if length <= 0:
            return None, "empty body"

        try:
            raw = self.rfile.read(length)
        except Exception as exc:
            return None, f"cannot read request body: {exc}"

        try:
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                return None, "invalid payload format"
        except Exception:
            return None, "invalid json"

        return payload, None

    def do_GET(self):
        parsed = urlparse(self.path)

        try:
            if parsed.path in ("/", "/index.html"):
                self._serve_index()
                return

            if parsed.path.startswith("/static/"):
                self._serve_static(parsed.path)
                return

            if parsed.path == "/api/info":
                with self.jobs.lock:
                    jobs_count = len(self.jobs.jobs)
                self._json(
                    200,
                    {
                        "storage_path": str(self.jobs.storage_path.resolve()),
                        "storage_link": str(self.jobs.storage_path),
                        "poll_interval": self.poll_interval,
                        "jobs_count": jobs_count,
                    },
                )
                return

            if parsed.path == "/api/heartbeat":
                now = time.time()
                with self.jobs.lock:
                    total_jobs = len(self.jobs.jobs)
                    active_jobs = [job for job in self.jobs.jobs.values() if job.status in {"queued", "starting", "running"}]
                    heartbeat_jobs = [
                        {
                            "id": job.id,
                            "status": job.status,
                            "heartbeat": job.heartbeat,
                            "updated_at": job.updated_at,
                            "attempts": f"{job.attempts}/{job.attempts_total}",
                            "device": job.device,
                            "error": job.error,
                            "log_tail": job.log_tail,
                            "notes": getattr(job, "notes", []),
                            "current_command": job.current_command,
                        }
                        for job in active_jobs
                    ]
                self._json(
                    200,
                    {
                        "status": "ok",
                        "now": now,
                        "poll_interval": self.poll_interval,
                        "jobs_total": total_jobs,
                        "active_jobs": heartbeat_jobs,
                        "storage_path": str(self.jobs.storage_path.resolve()),
                    },
                )
                return

            if parsed.path == "/api/drives":
                try:
                    drives = []
                    for drive in self.scanner.list_drives():
                        try:
                            enc = detect_encryption(drive["device"])
                        except Exception as exc:
                            logging.warning("encryption detection failed for %s: %s", drive.get("device"), exc)
                            enc = {"encrypted": None, "method": "unknown", "error": str(exc)}
                        drives.append({**drive, **{"encryption": enc}})
                except Exception as exc:
                    logging.exception("failed to list drives")
                    self._respond_error(500, "unable to enumerate drives", str(exc))
                    return
                self._json(200, {"drives": drives})
                return

            if parsed.path == "/api/jobs":
                self._json(200, {"jobs": self.jobs.list_jobs()})
                return

            if parsed.path == "/api/files":
                self._json(200, {"files": self.jobs.list_files()})
                return

            if parsed.path.startswith("/download/"):
                filename = unquote(parsed.path.split("/download/", 1)[1])
                safe_name = Path(filename).name
                candidate = (self.jobs.storage_path / safe_name).resolve()
                storage_root = self.jobs.storage_path.resolve()

                if not safe_name.endswith(".mp4") or not str(candidate).startswith(f"{storage_root}/"):
                    self._respond_error(400, "invalid file requested")
                    return
                if not candidate.exists():
                    self._respond_error(404, "file not found")
                    return
                if not candidate.is_file():
                    self._respond_error(400, "not a file")
                    return

                try:
                    size = candidate.stat().st_size
                except OSError as exc:
                    self._respond_error(400, "cannot access file", str(exc))
                    return

                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Disposition", f'attachment; filename="{candidate.name}"')
                self.send_header("Content-Length", str(size))
                self.end_headers()

                try:
                    with candidate.open("rb") as f:
                        while True:
                            chunk = f.read(1024 * 1024)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                except Exception as exc:
                    logging.exception("download failed for %s", candidate)
                    # Response may have started; best effort log only
                    self._append_log_error(f"download failed: {exc}")
                return

            self._respond_error(404, "not found")
        except Exception as exc:
            logging.exception("Unhandled GET error for %s", self.path)
            self._respond_error(500, "internal server error", str(exc))

    def _append_log_error(self, message: str) -> None:
        try:
            logging.error(message)
        except Exception:
            pass

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/rip":
            self._respond_error(404, "not found")
            return

        payload, error = self._read_json()
        if error:
            self._respond_error(400, error)
            return

        device = (payload or {}).get("device") if payload else None
        title = (payload or {}).get("title") if payload else None
        mode = (payload or {}).get("mode") if payload else "normal"
        if not isinstance(mode, str):
            mode = "normal" if not bool(mode) else "engineer"

        if not device:
            self._respond_error(400, "device required")
            return

        try:
            job_id = self.jobs.create_job(device, title=title, mode=mode)
        except Exception as exc:
            logging.exception("failed to start rip")
            self._respond_error(500, "failed to start job", str(exc))
            return

        self._json(200, {"job_id": job_id})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/rip/"):
            self._respond_error(404, "not found")
            return

        job_id = parsed.path.rsplit("/", 1)[-1]
        if not job_id:
            self._respond_error(400, "job_id required")
            return

        if self.jobs.cancel_job(job_id):
            self._json(200, {"ok": True, "job_id": job_id})
            return

        self._respond_error(404, "job not found or already finished")


class DVWebServer(ThreadingHTTPServer):
    def __init__(self, address, request_handler, settings, scanner: DriveScanner, jobs: RipManager):
        super().__init__(address, request_handler)
        request_handler.scanner = scanner
        request_handler.jobs = jobs
        request_handler.poll_interval = settings.poll_interval
