from __future__ import annotations

import logging
import os
import json
import re
import shutil
import signal
import subprocess
import shlex
import threading
import time
import traceback
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .common import run_cmd
from .native_probe import analyze_sample
from .native_dvd_reader import is_native_dvd_dump_available
from .native_homebrew import is_homebrew_available
from .native_go_runner import is_go_runner_available
from .extraction import BuildProfile, DvdExtractionPlanBuilder
from .execution import CommandAttemptDispatcher, DvdRetryPlanner
from .models import RipJob


class RipManager:
    MIN_OUTPUT_BYTES = 1 * 1024 * 1024
    DEFAULT_CMD_TIMEOUT_SECONDS = 60 * 20
    COMMAND_IDLE_POLL_SECONDS = 0.25
    MAX_STALL_READ_ITERATIONS = 1200
    MIN_OUTPUT_DURATION_SECONDS = 1.0
    MAX_ATTEMPT_FAILURE_LINES = 40
    MAX_LOG_LINES = 240
    SOURCE_PROBE_BYTES = 128 * 1024

    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.ffmpeg = shutil.which("ffmpeg")
        self.ffprobe = shutil.which("ffprobe")
        self.handbrake = shutil.which("HandBrakeCLI")
        self.ffmpeg_supports_dvd_device = self._supports_option("dvd_device")
        self._ffmpeg_formats = self._detect_ffmpeg_formats()
        self.ffmpeg_supports_dvd = "dvd" in self._ffmpeg_formats and self._supports_protocol("dvd")
        self.ffmpeg_supports_dvd_protocol = self._supports_protocol("dvd")
        self.ffmpeg_supports_mpeg = "mpeg" in self._ffmpeg_formats or self.ffmpeg_supports_dvd
        self.debug_enabled = os.environ.get("DVD_EXTRACT_DEBUG", "1").lower() in {"1", "true", "on", "yes"}
        self.native_dump_available = is_native_dvd_dump_available()
        self.homebrew_available = is_homebrew_available()
        self.go_runner_available = is_go_runner_available()
        if self.native_dump_available:
            logging.info("Native libdvdread dumper enabled")
        if self.homebrew_available:
            logging.info("C++ homebrew preprocessor enabled")
        if self.go_runner_available:
            logging.info("Go homebrew runner enabled")
        self.jobs: Dict[str, RipJob] = {}
        self.lock = threading.Lock()
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._source_probe_cache: Dict[str, tuple[bool, str | None]] = {}
        self.plan_builder = DvdExtractionPlanBuilder(BuildProfile(self))
        self.command_runner = CommandAttemptDispatcher(self)
        self.retry_planner = DvdRetryPlanner(self)

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
        self._set_job_state(
            job_id,
            status="starting",
            error=None,
            log_tail="Préparation stratégie d'extraction...",
        )
        self._append_job_note(job_id, "Préparation stratégie d'extraction")

        base_device = device if device.startswith("/dev/") else f"/dev/{device}"

        try:
            commands = self._build_ffmpeg_commands(device, output_path, mode=mode)
        except Exception as exc:
            self._fail_job(job_id, "failed to build extraction commands", str(exc))
            return

        if not commands:
            self._fail_job(job_id, "no extraction strategy prepared")
            return

        self._append_job_note(job_id, f"Plan prêt: {len(commands)} stratégie(s)")

        last_error: str | None = None
        total_attempts = len(commands)

        attempt_idx = 0
        while attempt_idx < len(commands):
            attempt_idx += 1
            command = commands[attempt_idx - 1]
            if self._is_cancelled(job_id):
                self._cancel_job(job_id)
                self._cleanup_command_artifacts(command)
                return

            self._safe_unlink(output_path)

            label = str(command.get("label", "default"))
            cmd_display = self._format_command_preview(command.get("argv", []))
            if command.get("tool") == "pipeline" and not cmd_display:
                cmd_display = self._format_pipeline_preview(command.get("pipeline"))
            self._set_job_state(
                job_id,
                status="running",
                error=None,
                progress=0.0,
                started_at=time.time(),
                attempts=attempt_idx,
                attempts_total=total_attempts,
                current_command=cmd_display,
                heartbeat=time.time(),
            )
            self._append_job_tail(job_id, f"Tentative {attempt_idx}/{total_attempts} — {label}")
            self._append_job_tail(job_id, f"Commande: {cmd_display}")
            self._append_job_note(job_id, f"Tentative {attempt_idx}/{total_attempts}: {label}")

            try:
                return_code, attempt_error = self._run_attempt(job_id, command)
            except Exception as exc:
                last_error = f"attempt {attempt_idx} runtime error: {exc}"
                self._append_job_tail(job_id, last_error)
                self._append_job_tail(job_id, traceback.format_exc())
                self._safe_unlink(output_path)
                self._cleanup_command_artifacts(command)
                continue

            if self._is_cancelled(job_id):
                self._cancel_job(job_id)
                self._cleanup_command_artifacts(command)
                return

            if return_code == 0 and self._verify_output(output_path):
                self._cleanup_command_artifacts(command)
                self._complete_job(job_id)
                self._append_job_note(job_id, "Extraction terminée avec succès")
                self._append_job_tail(job_id, "Extraction terminée avec succès")
                return

            self._safe_unlink(output_path)
            self._cleanup_command_artifacts(command)

            synthetic_error: str | None = None
            if return_code == 0:
                output_size = output_path.stat().st_size if output_path.exists() else 0
                last_error = f"tentative {attempt_idx} terminé mais fichier non valide"
                if mode in {"engineer", "advanced"}:
                    synthetic_error = (
                        f"verify-output-failed: file={Path(output_path).name} "
                        f"size={output_size}"
                    )
            elif attempt_error:
                last_error = attempt_error
            else:
                last_error = f"tentative {attempt_idx} échouée (code {return_code})"

            self._set_job_state(job_id, status="running", error=last_error, progress=0.0)
            self._append_job_tail(job_id, last_error)
            self._append_job_note(job_id, last_error)
            if attempt_idx < total_attempts:
                self._append_job_tail(job_id, "Passage au mode de rip suivant.")

            # Mode ingénieur: ajouter dynamiquement des tentatives supplémentaires
            # si une option échoue clairement pour tester un contournement directement.
            retry_error = attempt_error or synthetic_error
            if mode in {"engineer", "advanced"} and retry_error:
                retries = self._derive_retry_attempts(
                    command,
                    retry_error,
                    output=str(output_path),
                    base_device=base_device,
                )
                if retries:
                    commands[attempt_idx:attempt_idx] = retries
                    total_attempts = len(commands)
                    self._set_job_state(job_id, attempts_total=total_attempts)
                    self._append_job_tail(job_id, f"Ajustement ingénieur: +{len(retries)} tentative(s) additionnelle(s)")
                    self._append_job_note(job_id, f"Ajouter {len(retries)} tentative(s) ciblée(s)")

        if self._is_cancelled(job_id):
            self._cancel_job(job_id)
            return

        self._fail_job(job_id, "all attempts failed", last_error)

    def _run_attempt(self, job_id: str, command: dict) -> Tuple[int | None, str | None]:
        return self.command_runner.run(job_id, command)

    def _derive_retry_attempts(self, command: dict, attempt_error: str, output: str, base_device: str) -> list[dict]:
        try:
            return self.retry_planner.derive_retry_attempts(command, attempt_error, output, base_device)
        except Exception as exc:
            logging.exception("retry planner failed")
            logging.error("retry planner failed for %s: %s", base_device, exc)
            return []

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

        self._append_job_note(job_id, f"Échec final: {message}")

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

        self._append_job_note(job_id, "Job terminé avec succès")

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
                if self._normalize_log_line(lines[-1]) == self._normalize_log_line(line):
                    return
            else:
                lines = []

            new_tail = f"{previous}\n{line}" if previous else line
            tail_lines = new_tail.splitlines()
            if len(tail_lines) > self.MAX_LOG_LINES:
                tail_lines = tail_lines[-self.MAX_LOG_LINES:]
            job.log_tail = "\n".join(tail_lines)
            job.updated_at = time.time()

    def _append_job_note(self, job_id: str, note: str) -> None:
        if not note:
            return

        clean = str(note).strip()
        if not clean:
            return

        timestamped = f"[{datetime.now().strftime('%H:%M:%S')}] {clean}"
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return

            job.notes.append(timestamped)
            if len(job.notes) > 120:
                job.notes = job.notes[-120:]

    @staticmethod
    def _format_command_preview(argv: List[str]) -> str:
        if not argv:
            return "-"
        safe = [shlex.quote(item) for item in argv]
        preview = " ".join(safe)
        return preview[:900]

    @staticmethod
    def _format_pipeline_preview(pipeline: list[dict] | None) -> str:
        if not pipeline:
            return "-"

        parts: list[str] = []
        for item in pipeline:
            if not isinstance(item, dict):
                continue
            argv = item.get("argv")
            if not isinstance(argv, list):
                continue
            tool = (item.get("tool") or "").strip()
            if tool:
                parts.append(tool)
            if argv:
                parts.append(shlex.quote(str(argv[0])))

        return " | ".join(parts) if parts else "pipeline"

    def _summarize_error(self, error_lines: List[str], return_code: int, tool: str) -> str:
        if not error_lines:
            if tool == "ffmpeg":
                return f"ffmpeg exited with code {return_code}"
            if return_code:
                return f"{tool} exited with code {return_code}"
            return ""

        lowered = [line.lower() for line in error_lines]
        for line in reversed(error_lines):
            lower = line.lower()
            if "not found" in lower and "option" in lower:
                return line
            if "unrecognized" in lower and "option" in lower:
                return line
            if "error splitting" in lower:
                return line
            if "invalid argument" in lower:
                return line
            if "permission denied" in lower:
                return (
                    f"{line} | Permission refusee: relance le serveur avec les droits adaptes "
                    "ou corrige le dossier cible avec `sudo chown -R $(id -un) <dossier>`."
                )
            if "unable" in lower:
                return line

        return error_lines[-1]

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
            if not output_path.exists() or output_path.stat().st_size < self.MIN_OUTPUT_BYTES:
                return False
        except OSError as exc:
            logging.warning("failed to verify output %s: %s", output_path, exc)
            return False

        if not self.ffprobe:
            return True

        command = [
            self.ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(output_path),
        ]

        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=15,
            )
            if result.returncode != 0:
                logging.warning("ffprobe failed for %s (code=%s)", output_path, result.returncode)
                return False

            payload = json.loads(result.stdout or "{}")
            streams = payload.get("streams", [])
            has_video = any(item.get("codec_type") == "video" for item in streams)
            if not has_video:
                logging.warning("ffprobe report no video stream for %s", output_path)
                return False

            fmt = payload.get("format", {}) or {}
            duration = float(fmt.get("duration", "0") or 0)
            if duration < self.MIN_OUTPUT_DURATION_SECONDS:
                logging.warning("ffprobe duration too short for %s: %.3fs", output_path, duration)
                return False

            return True
        except json.JSONDecodeError:
            logging.warning("ffprobe output invalid JSON for %s", output_path)
            return False
        except Exception as exc:
            logging.warning("ffprobe exception for %s: %s", output_path, exc)
            return False

    def _build_ffmpeg_source_commands(
        self,
        commands: list[dict],
        source: str,
        label: str,
        output: str,
        input_args: list[str],
        engineer_mode: bool,
        command_timeout: int | None = None,
    ) -> None:
        return self.plan_builder.build_ffmpeg_source_commands(
            commands,
            source,
            label,
            output,
            input_args,
            engineer_mode,
            command_timeout,
        )

    def _build_ffmpeg_commands(self, device: str, output_path: Path, mode: str = "normal") -> list[dict]:
        return self.plan_builder.build(device, output_path, mode)

    def _build_native_dump_pipelines(self, source_candidates: list[str], output: str) -> list[dict]:
        return self.plan_builder.build_native_dump_pipelines(source_candidates, output)

    def _mounted_volume(self, device: str) -> Optional[Path]:
        mounts = self._mounted_volume_candidates(device)
        return mounts[0] if mounts else None

    @staticmethod
    def _is_valid_media_probe(sample: dict | None) -> tuple[bool, str | None]:
        if not sample or not isinstance(sample, dict):
            return False, "no probe data"

        bytes_count = sample.get("bytes", 0)
        if not isinstance(bytes_count, int) or bytes_count <= 0:
            return False, "no bytes"

        entropy = sample.get("entropy")
        if not isinstance(entropy, (int, float)):
            return False, "entropy not available"

        pack_sync = sample.get("pack_sync_count", 0)
        ts_sync = sample.get("ts_sync_count", 0)
        if pack_sync > 0 or ts_sync > 0:
            return True, None

        if 0.5 <= float(entropy) <= 7.95:
            return True, "media probe heuristic: no clear MPEG sync"

        return False, f"low-confidence media signature entropy={entropy}"

    def _probe_source_access(self, source: str) -> tuple[bool, str | None]:
        if not source:
            return False, "empty source"

        cached = self._source_probe_cache.get(source)
        if cached is not None:
            return cached

        if source.startswith("/dev/"):
            result = Path(source).exists()
            info = (result, "device exists" if result else "device missing")
            self._source_probe_cache[source] = info
            return info

        path = Path(source)
        if not path.exists() or not path.is_file():
            info = (False, "source not a file")
            self._source_probe_cache[source] = info
            return info

        sample = analyze_sample(str(path), sample_bytes=self.SOURCE_PROBE_BYTES)
        probe_ok, reason = self._is_valid_media_probe(sample)
        if sample and probe_ok:
            info = (True, None)
            self._source_probe_cache[source] = info
            return info

        if sample:
            self._source_probe_cache[source] = (False, reason)
            if reason and "heuristic" in reason:
                info = (True, reason)
                self._source_probe_cache[source] = info
                return info

        try:
            with path.open("rb") as handle:
                data = handle.read(64 * 1024)
                ok = bool(data)
                info = (ok, None if ok else "empty sample")
                self._source_probe_cache[source] = info
                return info
        except Exception as exc:
            info = (False, str(exc))
            self._source_probe_cache[source] = info
            return info

    @staticmethod
    def _normalize_disk(device: str) -> str:
        if device.startswith("/dev/rdisk"):
            return f"/dev/disk{device.removeprefix('/dev/rdisk')}"
        if device.startswith("/dev/disk"):
            return device
        return device

    @staticmethod
    def _base_disk_id(device: str) -> str:
        return re.sub(r"s\d+$", "", device)

    def _mounted_volume_candidates(self, device: str) -> list[Path]:
        candidates = {self._normalize_disk(device), self._base_disk_id(device), self._normalize_disk(self._base_disk_id(device))}
        mounts: list[Path] = []

        result = run_cmd(["mount"], timeout=8)
        if result.return_code == 0:
            for line in result.stdout.splitlines():
                left, _, right = line.partition(" on ")
                if not right:
                    continue
                mountpoint = right.split(" (", 1)[0].strip()
                source = left.strip()
                if not mountpoint.startswith("/Volumes/"):
                    continue
                if any(source == candidate or source.startswith(f"{candidate}s") for candidate in candidates):
                    path = Path(mountpoint)
                    if path.exists():
                        mounts.append(path)

        if not mounts:
            volumes_root = Path("/Volumes")
            if volumes_root.exists():
                for vol in volumes_root.iterdir():
                    if not vol.is_dir():
                        continue
                    if (vol / "VIDEO_TS").is_dir():
                        mounts.append(vol)

        dedup: list[Path] = []
        seen: set[str] = set()
        for path in mounts:
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            if path.exists():
                dedup.append(path)
        return dedup

    def _build_vob_title_commands(self, mount_point: Path, output: str) -> list[dict]:
        return self.plan_builder.build_vob_title_commands(mount_point, output)

    def _build_go_runner_vob_commands(
        self,
        title_id: int,
        parts: list[Path],
        output: str,
        mount_point: Optional[Path],
    ) -> list[dict]:
        return self.plan_builder.build_go_runner_vob_commands(title_id, parts, output, mount_point)

    def _build_homebrew_vob_commands(
        self,
        title_id: int,
        parts: list[Path],
        output: str,
        mount_point: Path,
    ) -> list[dict]:
        return self.plan_builder.build_homebrew_vob_commands(title_id, parts, output, mount_point)

    def _scan_vob_titles(self, video_ts: Path) -> list[tuple[int, list[Path]]]:
        return self.plan_builder.scan_vob_titles(video_ts)

    def _vob_has_video(self, file: Path) -> bool:
        return self.plan_builder.vob_has_video(file)

    def _build_vob_concat_commands(
        self,
        title_id: int,
        parts: list[Path],
        output: str,
        mount_point: Optional[Path] = None,
    ) -> list[dict]:
        return self.plan_builder.build_vob_concat_commands(title_id, parts, output, mount_point)

    def _safe_file_size(self, path: Path) -> int:
        return self.plan_builder.safe_file_size(path)

    def _detect_ffmpeg_formats(self) -> set[str]:
        if not self.ffmpeg:
            return set()

        result = run_cmd([self.ffmpeg, "-hide_banner", "-formats"], timeout=10)
        if result.return_code != 0:
            return set()

        formats: set[str] = set()
        for line in (result.stdout or "").splitlines():
            text = line.rstrip()
            if not text or len(text) < 5:
                continue
            parts = text.split()
            if len(parts) < 2:
                continue
            flags = parts[0]
            if not re.match(r"^[\.\sDE]+$", flags):
                continue
            if "D" not in flags:
                continue

            fmt = parts[1].lower()
            if fmt:
                formats.add(fmt)

        return formats

    @staticmethod
    def _ensure_video_only_args(argv: list[str], output: str) -> list[str]:
        if not argv:
            return ["-an", output]

        cleaned = list(argv)
        if cleaned and cleaned[-1] == output:
            cleaned = cleaned[:-1]

        output_opts: list[str] = []
        skip_next = False
        for index, token in enumerate(cleaned):
            if skip_next:
                skip_next = False
                continue

            if token in {"-c:a", "-b:a", "-ac", "-ab", "-ar", "-disposition:a"}:
                skip_next = True
                continue

            if token == "-map" and index + 1 < len(cleaned) and str(cleaned[index + 1]).startswith("0:a"):
                skip_next = True
                continue

            if token == "-an":
                continue

            output_opts.append(token)

        if "-an" not in output_opts:
            output_opts.append("-an")

        output_opts.append(output)
        return output_opts

    @staticmethod
    def _strip_audio_from_args(argv: list[str]) -> list[str]:
        result: list[str] = []
        skip_next = False
        for index, token in enumerate(argv):
            if skip_next:
                skip_next = False
                continue

            if token in {"-c:a", "-b:a", "-ac", "-ab", "-ar"}:
                skip_next = True
                continue

            if token == "-map" and index + 1 < len(argv) and str(argv[index + 1]).startswith("0:a"):
                skip_next = True
                continue

            if token == "-an":
                continue

            if token == "-copyts":
                continue

            result.append(token)

        return result

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
                timeout=10,
            )
        except Exception:
            return False

        prefix = option.lower().lstrip("-")
        for line in (result.stdout or "").splitlines():
            token = line.strip().lower().split(maxsplit=1)[0] if line.strip().startswith("-") else ""
            if not token:
                continue
            token = token.lstrip("-")
            if token == prefix:
                return True
            if token.startswith(f"{prefix}="):
                return True
        return False

    def _supports_protocol(self, protocol: str) -> bool:
        if not self.ffmpeg:
            return False

        try:
            result = subprocess.run(
                [self.ffmpeg, "-hide_banner", "-protocols"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=10,
            )
        except Exception:
            return False

        protocol = protocol.lower()
        text = (result.stdout or "").lower()
        if "input:" not in text:
            return False

        input_section = text.split("input:", 1)[1].split("output:", 1)[0]
        tokens = re.findall(r"[a-z0-9_]+", input_section)
        return protocol in tokens

    @staticmethod
    def _alt_device(device: str) -> str:
        if device.startswith("/dev/rdisk"):
            return f"/dev/disk{device.removeprefix('/dev/rdisk')}"
        if device.startswith("/dev/disk"):
            return f"/dev/rdisk{device.removeprefix('/dev/disk')}"
        return ""

    @staticmethod
    def _normalize_log_line(line: str) -> str:
        normalized = str(line).strip().lower()
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.replace("  ", " ").strip()

    @staticmethod
    def _log_contains(log_tail: str, target: str) -> bool:
        if not log_tail or not target:
            return False
        normalized_target = RipManager._normalize_log_line(target)
        return any(RipManager._normalize_log_line(line) == normalized_target for line in log_tail.splitlines())

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
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"{slug}-{timestamp}.mp4"
