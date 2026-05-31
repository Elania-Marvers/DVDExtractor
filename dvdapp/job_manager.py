from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import shlex
import threading
import time
import traceback
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .common import run_cmd


@dataclass
class RipJob:
    id: str
    device: str
    output: str
    status: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    progress: float | None = None
    log_tail: str = ""
    error: str | None = None
    pid: int | None = None
    attempts: int = 0
    attempts_total: int = 0
    current_command: str | None = None
    heartbeat: float = field(default_factory=time.time)
    mode: str = "normal"


class RipManager:
    MIN_OUTPUT_BYTES = 5 * 1024 * 1024
    MAX_ATTEMPT_FAILURE_LINES = 30

    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.ffmpeg = shutil.which("ffmpeg")
        self.handbrake = shutil.which("HandBrakeCLI")
        self.ffmpeg_supports_dvd_device = self._supports_option("dvd_device")
        self.debug_enabled = os.environ.get("DVD_EXTRACT_DEBUG", "1").lower() in {"1", "true", "on", "yes"}
        self.jobs: Dict[str, RipJob] = {}
        self.lock = threading.Lock()
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.max_tail_lines = 60

    def list_jobs(self) -> List[dict]:
        with self.lock:
            return [self._serialize(job) for job in sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)]

    def list_files(self) -> List[dict]:
        files: List[dict] = []
        for mp4 in sorted(self.storage_path.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                size = mp4.stat().st_size
                modified = datetime.fromtimestamp(mp4.stat().st_mtime).isoformat()
            except OSError as exc:
                logging.warning("cannot read file metadata for %s: %s", mp4, exc)
                continue
            files.append(
                {
                    "name": mp4.name,
                    "size": size,
                    "modified": modified,
                    "url": f"/download/{mp4.name}",
                }
            )
        return files

    def create_job(self, device: str, title: Optional[str] = None, mode: str = "normal") -> str:
        if not self.ffmpeg:
            raise RuntimeError("ffmpeg not found in PATH")
        mode = (mode or "normal").strip().lower()
        if mode not in {"normal", "engineer", "advanced"}:
            mode = "normal"

        try:
            out_name = self._make_name(device, title)
            output_path = (self.storage_path / out_name).resolve()
        except Exception as exc:
            raise RuntimeError("invalid output path") from exc

        job_id = uuid.uuid4().hex
        job = RipJob(id=job_id, device=device, output=str(output_path), status="queued", mode=mode)

        with self.lock:
            self.jobs[job_id] = job

        try:
            thread = threading.Thread(
                target=self._run_job,
                args=(job_id, device, output_path, mode),
                daemon=True,
            )
            thread.start()
        except Exception as exc:
            self._fail_job(job_id, "unable to start extraction thread", str(exc))
            raise

        return job_id

    def get_job(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return self._serialize(job) if job else None

    def cancel_job(self, job_id: str) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return False

            if job.status in {"completed", "failed", "cancelled"}:
                return False

            if job.status == "cancelling":
                return True

            job.status = "cancelling"
            job.updated_at = time.time()

            if not job.pid:
                job.status = "cancelled"
                job.finished_at = time.time()
                return True

            try:
                os.kill(job.pid, signal.SIGTERM)
            except ProcessLookupError:
                job.status = "cancelled"
                job.finished_at = time.time()
                return True
            except Exception:
                logging.exception("failed to request process cancel for pid=%s", job.pid)
                job.status = "failed"
                job.error = "cannot cancel job"
                return False

            return True

    def _run_job(self, job_id: str, device: str, output_path: Path, mode: str = "normal") -> None:
        self._set_job_state(job_id, status="starting", error=None, log_tail="Analyse de la chaîne FFmpeg...")

        try:
            commands = self._build_ffmpeg_commands(device, output_path, mode=mode)
        except Exception as exc:
            self._fail_job(job_id, "failed to build ffmpeg commands", str(exc))
            return

        if not commands:
            self._fail_job(job_id, "no ffmpeg command could be prepared")
            return

        last_error: str | None = None

        for attempt_idx, command in enumerate(commands, start=1):
            if self._is_cancelled(job_id):
                self._cancel_job(job_id)
                return

            self._safe_unlink(output_path)
            label = command.get("label", "default")
            argv = command["argv"]
            cmd_display = self._format_command_preview(argv)
            attempt_total = len(commands)
            self._set_job_state(
                job_id,
                status="running",
                error=None,
                progress=None,
                started_at=time.time(),
                attempts=attempt_idx,
                attempts_total=attempt_total,
                current_command=cmd_display,
                heartbeat=time.time(),
            )
            self._append_job_tail(
                job_id,
                f"Tentative {attempt_idx}/{attempt_total} — {label} — {cmd_display}",
            )

            try:
                return_code, attempt_error = self._run_ffmpeg_attempt(job_id, argv)
            except Exception as exc:
                last_error = f"attempt {attempt_idx} runtime error: {exc}"
                self._append_job_tail(job_id, last_error)
                self._append_job_tail(job_id, traceback.format_exc())
                self._safe_unlink(output_path)
                self._cleanup_command_artifacts(command)
                continue

            if self._is_cancelled(job_id):
                self._cancel_job(job_id)
                return

            if return_code == 0 and self._verify_output(output_path):
                self._complete_job(job_id)
                self._append_job_tail(job_id, "Extraction terminée avec succès")
                return

            self._cleanup_command_artifacts(command)

            if return_code == 0:
                last_error = f"attempt {attempt_idx} exit 0 but output not usable"
            elif attempt_error:
                last_error = attempt_error
            else:
                last_error = f"attempt {attempt_idx} failed (ffmpeg exit {return_code})"
            if attempt_error:
                attempt_summary = attempt_error
            else:
                attempt_summary = f"tentative {attempt_idx} échouée (code {return_code})"

            if output_path.exists():
                self._safe_unlink(output_path)

            self._set_job_state(job_id, status="running", error=attempt_summary, progress=0.0)
            self._append_job_tail(job_id, attempt_summary)
            if attempt_idx < attempt_total:
                self._append_job_tail(job_id, "Plan B: prochain mode de rip en cours d’essai.")

            if self._is_cancelled(job_id):
                self._cancel_job(job_id)
                return

        if self._is_cancelled(job_id):
            self._cancel_job(job_id)
            return

        self._fail_job(job_id, "all FFmpeg attempts failed", last_error)

    def _run_ffmpeg_attempt(self, job_id: str, command: List[str]) -> Tuple[int | None, str | None]:
        try:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            raise RuntimeError(f"cannot launch ffmpeg process: {exc}") from exc

        self._set_job_state(job_id, pid=proc.pid)
        duration_seconds: float | None = None
        last_line_error: str | None = None

        try:
            if proc.stderr is None:
                raise RuntimeError("missing stderr stream")
            seen_errors: List[str] = []

            for line in proc.stderr:
                if line is None:
                    continue

                clean = str(line).rstrip()
                self._append_job_tail(job_id, clean)
                self._heartbeat_job(job_id)
                if clean.strip():
                    lower = clean.lower()
                    if len(seen_errors) < self.MAX_ATTEMPT_FAILURE_LINES and (
                        "error" in lower or "failed" in lower or "invalid" in lower or "unable" in lower
                    ):
                        seen_errors.append(clean)

                if "Duration:" in clean:
                    duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", clean)
                    if duration:
                        duration_seconds = (
                            int(duration.group(1)) * 3600 + int(duration.group(2)) * 60 + float(duration.group(3))
                        )

                if duration_seconds:
                    match = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", clean)
                    if match:
                        current = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
                        progress = min(100.0, max(0.0, (current / duration_seconds) * 100.0))
                        self._set_job_state(job_id, progress=progress)

                if self._is_cancelled(job_id):
                    self._request_cancelled_process(proc)
                    break

            return_code = proc.wait()
            self._heartbeat_job(job_id)
            if proc.returncode != 0:
                self._append_job_tail(job_id, f"ffmpeg exited with code {proc.returncode}")
                last_line_error = self._summarize_ffmpeg_error(seen_errors, proc.returncode)
        except Exception as exc:
            self._request_cancelled_process(proc)
            raise RuntimeError(f"failed reading ffmpeg output: {exc}") from exc
        finally:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
            except Exception:
                logging.exception("failed to finalize ffmpeg process for job=%s", job_id)

            self._set_job_state(job_id, pid=None)

        return proc.returncode, last_line_error

    def _set_job_state(self, job_id: str, **fields) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return

            for key, value in fields.items():
                setattr(job, key, value)
            if "status" in fields and fields["status"] in {"running", "starting"}:
                job.heartbeat = time.time()

            if fields:
                job.updated_at = time.time()

    def _heartbeat_job(self, job_id: str) -> None:
        self._set_job_state(job_id, heartbeat=time.time())

    def _cleanup_command_artifacts(self, command: dict) -> None:
        artifacts = command.get("artifacts", [])
        if not artifacts:
            return

        for artifact in artifacts:
            try:
                Path(artifact).unlink(missing_ok=True)
            except Exception:
                logging.debug("failed to remove temp artifact %s", artifact)

    def _fail_job(self, job_id: str, message: str, *extra: str | None) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            if job.status == "cancelled":
                return
            job.status = "failed"
            full = " ".join(s for s in (message, *(str(e) for e in extra if e)) if s)
            job.error = full.strip()
            if full and not self._log_contains(job.log_tail, full):
                self._append_job_tail(job_id, full)
            job.pid = None
            job.progress = None
            job.finished_at = time.time()
            job.updated_at = time.time()

    def _complete_job(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            job.status = "completed"
            job.progress = 100.0
            job.error = None
            job.pid = None
            job.finished_at = time.time()
            job.updated_at = time.time()

    def _cancel_job(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            job.status = "cancelled"
            job.pid = None
            job.finished_at = time.time()
            job.updated_at = time.time()

    def _is_cancelled(self, job_id: str) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            return job.status == "cancelling"

    def _append_job_tail(self, job_id: str, line: str) -> None:
        if not line:
            return
        if not isinstance(line, str):
            line = str(line)
        line = line.strip()
        if not line:
            return
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            if self.debug_enabled:
                line = f"{datetime.now().strftime('%H:%M:%S')} {line}"
            previous = (job.log_tail or "").strip()
            if previous:
                lines = previous.splitlines()
                if lines and self._normalize_log_line(lines[-1]) == self._normalize_log_line(line):
                    return
            else:
                lines = []
            if line.startswith("- "):
                line = line[2:]
            combined = f"{previous}\n{line}" if previous else line
            tail_lines = combined.splitlines()
            if len(tail_lines) > self.max_tail_lines:
                tail_lines = tail_lines[-self.max_tail_lines:]
            job.log_tail = "\n".join(tail_lines)
            job.updated_at = time.time()

    @staticmethod
    def _format_command_preview(argv: List[str]) -> str:
        if not argv:
            return "-"
        safe = [shlex.quote(item) for item in argv]
        return " ".join(safe[:12]) + (" …" if len(safe) > 12 else "")

    def _log_contains(self, log_tail: str, target: str) -> bool:
        if not log_tail or not target:
            return False
        normalized_target = self._normalize_log_line(target)
        return any(self._normalize_log_line(line) == normalized_target for line in log_tail.splitlines())

    @staticmethod
    def _normalize_log_line(line: str) -> str:
        normalized = str(line).strip().lower()
        normalized = normalized.replace("  ", " ")
        normalized = normalized.replace(" : ", ":")
        normalized = normalized.replace(" :  ", ":")
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    def _summarize_ffmpeg_error(self, error_lines: List[str], return_code: int) -> str:
        if not error_lines:
            return f"ffmpeg exited with code {return_code}"

        # Keep the most actionable line near the end.
        for line in reversed(error_lines):
            lower = line.lower()
            if "option" in lower and "not found" in lower:
                return line
            if "unrecognized" in lower and "option" in lower:
                return line
            if "error splitting" in lower:
                return line
            if "invalid argument" in lower:
                return line

        return error_lines[-1] or f"ffmpeg exited with code {return_code}"

    def _request_cancelled_process(self, proc: subprocess.Popen[str] | None) -> None:
        if not proc:
            return
        if proc.poll() is not None:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                return
            except Exception:
                continue

            for _ in range(5):
                if proc.poll() is not None:
                    return
                time.sleep(0.1)

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        if not path.exists():
            return
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            logging.warning("unable to remove stale output %s: %s", path, exc)

    def _verify_output(self, output_path: Path) -> bool:
        try:
            if not output_path.exists():
                return False
            return output_path.stat().st_size >= self.MIN_OUTPUT_BYTES
        except OSError as exc:
            logging.warning("failed to verify output %s: %s", output_path, exc)
            return False

    def _build_ffmpeg_commands(
        self, device: str, output_path: Path, mode: str = "normal"
    ) -> list[dict]:
        output = str(output_path)
        target = "/dev/disk"
        alt = "/dev/rdisk"

        if device.startswith("/dev/rdisk"):
            alt_device = f"{target}{device.removeprefix('/dev/rdisk')}"
        elif device.startswith("/dev/disk"):
            alt_device = f"{alt}{device.removeprefix('/dev/disk')}"
        else:
            alt_device = ""

        input_base = [
            self.ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-analyzeduration",
            "30M",
            "-probesize",
            "30M",
        ]

        output_opts = [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
        ]

        tolerant_opts = ["-err_detect", "ignore_err", "-ignore_unknown"]

        engineer_mode = mode == "engineer" or mode == "advanced"
        include_debug = os.environ.get("DVD_EXTRACT_DEBUG", "1").lower() in {"1", "true", "on", "yes"}
        if include_debug and engineer_mode:
            output_opts.extend(["-report", "1"])

        commands: list[dict[str, object]] = []
        direct_sources = [device]
        if alt_device:
            direct_sources.append(alt_device)
        direct_sources = list(dict.fromkeys(direct_sources))

        dvd_sources = ["dvd://", "dvd://1"]

        # 1) Le flux le plus stable: lecture brute du bloc device.
        for source in direct_sources:
            commands.append(
                {
                    "label": f"Lecture brute ({Path(source).name})",
                    "argv": [*input_base, *output_opts, "-i", source, output],
                }
            )

            commands.append(
                {
                    "label": f"Lecture dvdvideo brute ({Path(source).name})",
                    "argv": [*input_base, "-f", "dvdvideo", "-i", source, *output_opts, output],
                }
            )

        # 2) Tentatives navigation DVD (plus permissives sur certains FFmpeg/macOS).
        for source in dvd_sources:
            commands.append(
                {
                    "label": f"Navigation DVD ({source})",
                    "argv": [*input_base, "-i", source, *output_opts, output],
                }
            )
            commands.append(
                {
                    "label": f"Navigation DVD+dvdvideo ({source})",
                    "argv": [*input_base, "-f", "dvdvideo", "-i", source, *output_opts, output],
                }
            )

            if engineer_mode:
                commands.append(
                    {
                        "label": f"Navigation DVD tolérante ({source}, ffmpeg xerror off)",
                        "argv": [*input_base, *tolerant_opts, "-i", source, *output_opts, output],
                    }
                )
                commands.append(
                    {
                        "label": f"Navigation DVD (no transcode) ({source})",
                        "argv": [
                            *input_base,
                            "-i",
                            source,
                            "-c",
                            "copy",
                            "-map",
                            "0",
                            "-ignore_unknown",
                            output,
                        ],
                    }
                )

        # 3) Tentatives with dvd_device only when option exists.
        if self.ffmpeg_supports_dvd_device:
            commands.append(
                {
                    "label": f"dvd_device={device}",
                    "argv": [*input_base, "-dvd_device", device, "-i", "dvd://", *output_opts, output],
                }
            )
            commands.append(
                {
                    "label": f"dvd_device={device}#1",
                    "argv": [*input_base, "-dvd_device", device, "-i", "dvd://1", *output_opts, output],
                }
            )
            if alt_device:
                commands.append(
                    {
                        "label": f"dvd_device={alt_device}",
                        "argv": [*input_base, "-dvd_device", alt_device, "-i", "dvd://", *output_opts, output],
                    }
                )

        # 4) Fallback ingénieur: extraction depuis le média monté en VOB concat.
        if engineer_mode:
            mount_point = self._mounted_volume(device)
            if mount_point:
                for title_command in self._build_vob_concat_commands(mount_point, output):
                    commands.append(title_command)

        # 4) Dernier filet: copie directe, plus tolérant en cas d'erreur d'encode.
        commands.append(
            {
                "label": "Mode copie directe",
                "argv": [
                    self.ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-i",
                    device,
                    "-c",
                    "copy",
                    "-map",
                    "0",
                    output,
                ],
            }
        )

        if engineer_mode and self.handbrake:
            commands.append(
                {
                    "label": "HandBrake fallback",
                    "argv": [
                        self.handbrake,
                        "-i",
                        device,
                        "-o",
                        output,
                        "--preset",
                        "Very Fast 1080p30",
                        "-e",
                        "x264",
                        "--quality",
                        "22",
                    ],
                }
            )

        # remove duplicate commands while preserving first-to-last priority order
        unique: list[dict[str, list[str]]] = []
        seen: set[tuple[str, ...]] = set()
        for cmd in commands:
            key = tuple(cmd["argv"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(cmd)

        if not unique:
            raise RuntimeError("no ffmpeg commands prepared")

        return unique

    def _mounted_volume(self, device: str) -> Path | None:
        result = run_cmd(["diskutil", "info", device], timeout=8)
        if result.return_code != 0:
            return None

        mount = ""
        for line in result.stdout.splitlines():
            if "Mount Point" in line:
                try:
                    mount = line.split(":", 1)[1].strip()
                except Exception:
                    mount = ""
                if mount and mount != "Not mounted":
                    break
        if not mount:
            return None

        path = Path(mount)
        if not path.exists() or not path.is_dir():
            return None
        return path

    def _build_vob_concat_commands(self, mount_point: Path, output: str) -> list[dict[str, list[str] | list[str]]]:
        video_ts = mount_point / "VIDEO_TS"
        if not video_ts.is_dir():
            return []

        vob_files = sorted(video_ts.glob("VTS_*_*.VOB"))
        if not vob_files:
            return []

        title_parts: dict[int, list[Path]] = {}
        for file in vob_files:
            match = re.match(r"VTS_(\d{2})_(\d{2})\.VOB$", file.name, re.IGNORECASE)
            if not match:
                continue
            title_id = int(match.group(1))
            part_no = int(match.group(2))
            if part_no == 0:
                continue
            title_parts.setdefault(title_id, []).append(file)

        if not title_parts:
            return []

        # prioritize longest title set (likely main feature)
        ranked = sorted(
            ((title, sorted(parts, key=lambda p: p.name)) for title, parts in title_parts.items() if parts),
            key=lambda item: sum(p.stat().st_size for p in item[1]),
            reverse=True,
        )

        commands: list[dict[str, list[str] | list[str]]] = []
        for title_id, parts in ranked[:3]:
            if not parts:
                continue
            tmp_fd, tmp_name = tempfile.mkstemp(prefix=f"dvdvob_{title_id}_", suffix=".txt")
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
                    for part in parts:
                        handle.write(f"file '{part.as_posix()}'\n")
            except Exception:
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
                continue

            input_opts = [*["-f", "concat", "-safe", "0"], "-i", str(tmp_path)]
            commands.append(
                {
                    "label": f"Concat VOB titre VTS_{title_id:02d}",
                    "argv": [
                        self.ffmpeg,
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "warning",
                        *input_opts,
                        "-c:v",
                        "libx264",
                        "-preset",
                        "medium",
                        "-crf",
                        "20",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "256k",
                        "-movflags",
                        "+faststart",
                        "-map",
                        "0:v:0?",
                        "-map",
                        "0:a:0?",
                        output,
                    ],
                    "artifacts": [str(tmp_path)],
                }
            )
            commands.append(
                {
                    "label": f"Concat VOB titre VTS_{title_id:02d} (copy)",
                    "argv": [
                        self.ffmpeg,
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "warning",
                        *input_opts,
                        "-c",
                        "copy",
                        "-map",
                        "0",
                        output,
                    ],
                    "artifacts": [str(tmp_path)],
                },
            )

        return commands

    def _supports_option(self, option: str) -> bool:
        if not self.ffmpeg:
            return False
        try:
            result = subprocess.run(
                [self.ffmpeg, "-hide_banner", "-h", "full"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=8,
            )
        except Exception:
            return False
        prefix = option.lower().lstrip("-")
        for line in (result.stdout or "").splitlines():
            line = line.strip().lower()
            if not line.startswith("-"):
                continue
            token = line.split(maxsplit=1)[0].lstrip("-")
            if token == prefix:
                return True
            if token.startswith(prefix + "="):
                return True
        return False

    def _serialize(self, job: RipJob | None) -> dict | None:
        if not job:
            return None
        data = asdict(job)
        data["created_at"] = datetime.fromtimestamp(data["created_at"]).isoformat()
        if data["started_at"]:
            data["started_at"] = datetime.fromtimestamp(data["started_at"]).isoformat()
        if data["finished_at"]:
            data["finished_at"] = datetime.fromtimestamp(data["finished_at"]).isoformat()
        data["updated_at"] = datetime.fromtimestamp(data["updated_at"]).isoformat()
        data["storage_path"] = str(Path(job.output).name)
        return data

    @staticmethod
    def _make_name(device: str, title: Optional[str] = None) -> str:
        source = title or Path(device).name or "dvd"
        slug = re.sub(r"[^a-zA-Z0-9._-]", "-", source)
        slug = slug.strip("-._")[:80]
        if not slug:
            slug = "dvd"
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"{slug}-{ts}.mp4"
