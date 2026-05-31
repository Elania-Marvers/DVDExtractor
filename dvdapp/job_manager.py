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
import tempfile
import traceback
import uuid
import select
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .common import run_cmd
from .vob_manifest import scan_vob_titles_from_video_ts
from .native_probe import analyze_sample
from .native_dvd_reader import (
    NativeTitleProbe,
    is_native_dvd_dump_available,
    list_title_candidates,
    dump_command_for_title,
)


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
    notes: list[str] = field(default_factory=list)


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
        self.lsdvd = shutil.which("lsdvd")
        self.ffmpeg_supports_dvd_device = self._supports_option("dvd_device")
        self._ffmpeg_formats = self._detect_ffmpeg_formats()
        self.ffmpeg_supports_dvd = "dvd" in self._ffmpeg_formats and self._supports_protocol("dvd")
        self.ffmpeg_supports_dvd_protocol = self._supports_protocol("dvd")
        self.ffmpeg_supports_mpeg = "mpeg" in self._ffmpeg_formats or self.ffmpeg_supports_dvd
        self.debug_enabled = os.environ.get("DVD_EXTRACT_DEBUG", "1").lower() in {"1", "true", "on", "yes"}
        self.native_dump_available = is_native_dvd_dump_available()
        if self.native_dump_available:
            logging.info("Native libdvdread dumper enabled")
        self.jobs: Dict[str, RipJob] = {}
        self.lock = threading.Lock()
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._source_probe_cache: Dict[str, tuple[bool, str | None]] = {}

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
        if not isinstance(command, dict):
            return 1, "invalid command definition"

        if command.get("tool") == "pipeline":
            return self._run_pipeline_attempt(job_id, command)

        if "pipeline" in command:
            return self._run_pipeline_attempt(job_id, command)

        argv = list(command.get("argv", []))
        if not argv:
            return 1, "empty command"

        timeout = int(command.get("timeout") or self.DEFAULT_CMD_TIMEOUT_SECONDS)
        tool_name = Path(str(argv[0])).name.lower()

        if tool_name == "dvd_reader_dump":
            return self._run_native_dump_attempt(job_id, command, timeout=timeout)

        if tool_name.endswith("handbrakecli"):
            return self._run_handbrake_attempt(job_id, argv, timeout=timeout)

        return self._run_ffmpeg_attempt(job_id, argv, timeout=timeout)

    def _run_pipeline_attempt(self, job_id: str, command: dict) -> Tuple[int | None, str | None]:
        steps = command.get("pipeline")
        if not isinstance(steps, list) or not steps:
            return 1, "invalid native pipeline"

        seen_errors: List[str] = []
        total_steps = len(steps)

        for idx, step in enumerate(steps, start=1):
            self._append_job_tail(job_id, f"Pipeline étape {idx}/{total_steps}")

            if not isinstance(step, dict) or "argv" not in step:
                msg = f"pipeline step {idx} missing argv"
                self._append_job_tail(job_id, msg)
                return 1, msg

            timeout = int(step.get("timeout") or command.get("timeout") or self.DEFAULT_CMD_TIMEOUT_SECONDS)
            tool = str(step.get("tool") or "").lower()
            argv = list(step["argv"])
            if tool == "dvd_reader_dump":
                return_code, step_error = self._run_native_dump_attempt(job_id, {**step, "timeout": timeout})
            elif tool == "ffmpeg":
                return_code, step_error = self._run_ffmpeg_attempt(job_id, argv, timeout=timeout)
            elif tool == "handbrake":
                return_code, step_error = self._run_handbrake_attempt(job_id, argv, timeout=timeout)
            else:
                return_code, step_error = self._run_ffmpeg_attempt(job_id, argv, timeout=timeout)

            if return_code != 0:
                if step_error:
                    return return_code, step_error
                return return_code, f"pipeline step failed ({tool or 'unknown'})"

            if step_error:
                seen_errors.append(step_error)

        return 0, seen_errors[-1] if seen_errors else None

    def _consume_lines(self, job_id: str, proc: subprocess.Popen[str], stream, on_line, timeout_seconds: int | None) -> bool:
        deadline = None
        if timeout_seconds and timeout_seconds > 0:
            deadline = time.time() + timeout_seconds

        stall_count = 0
        while True:
            if self._is_cancelled(job_id):
                self._request_cancelled_process(proc)
                break

            remaining = None
            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    self._append_job_tail(job_id, f"Process timeout after {timeout_seconds}s")
                    self._request_cancelled_process(proc)
                    return True

            timeout = self.COMMAND_IDLE_POLL_SECONDS if remaining is None else min(self.COMMAND_IDLE_POLL_SECONDS, max(0.05, remaining))
            if timeout is None:
                timeout = self.COMMAND_IDLE_POLL_SECONDS

            ready = False
            if stream is not None:
                try:
                    ready_list, _, _ = select.select([stream], [], [], timeout)
                    if ready_list:
                        ready = True
                except (OSError, ValueError):
                    ready = False

            if ready and stream is not None:
                line = stream.readline()
                if line:
                    on_line(line)
                    self._heartbeat_job(job_id)
                    stall_count = 0
                    continue

            poll = proc.poll()
            if poll is not None:
                break

            if not ready:
                stall_count += 1
                if stall_count > self.MAX_STALL_READ_ITERATIONS:
                    self._append_job_tail(job_id, "Process stalled, no output for too long")
                    self._request_cancelled_process(proc)
                    return True

            if self._is_cancelled(job_id):
                self._request_cancelled_process(proc)
                return True

        return False

    def _run_native_dump_attempt(
        self,
        job_id: str,
        command: dict,
        timeout: int | None = None,
    ) -> Tuple[int | None, str | None]:
        if timeout is None:
            timeout = self.DEFAULT_CMD_TIMEOUT_SECONDS
        argv = list(command.get("argv", []))
        if not argv:
            return 1, "empty native command"

        output_path = command.get("output_path")
        if not output_path:
            if "--output" in argv:
                idx = argv.index("--output")
                if idx + 1 < len(argv):
                    output_path = argv[idx + 1]
            elif len(argv) >= 1 and argv[-1].endswith(".vob"):
                output_path = argv[-1]

        output_file = Path(str(output_path)) if output_path else None

        seen_errors: List[str] = []

        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            raise RuntimeError(f"cannot launch native dump process: {exc}") from exc

        self._set_job_state(job_id, pid=proc.pid)

        def on_line(raw: str) -> None:
            if not raw:
                return
            clean = str(raw).rstrip()
            if not clean:
                return
            self._append_job_tail(job_id, clean)
            lower = clean.lower()
            if "dump_progress" in lower and "bytes=" in lower:
                self._set_job_state(job_id, progress=None)
            if len(seen_errors) < self.MAX_ATTEMPT_FAILURE_LINES and any(
                token in lower for token in ("error", "failed", "invalid", "cannot", "permission")
            ):
                seen_errors.append(clean)

        timed_out = self._consume_lines(job_id, proc, proc.stdout, on_line, timeout)
        try:
            return_code = proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._request_cancelled_process(proc)
            return_code = proc.wait(timeout=10)

        self._heartbeat_job(job_id)

        if output_file and output_file.exists() and output_file.stat().st_size <= 0:
            return_code = 18
            seen_errors.append("empty native dump output")

        if return_code != 0:
            if output_file:
                size = output_file.stat().st_size if output_file.exists() else 0
                self._append_job_tail(job_id, f"dvd_reader_dump exited with code {return_code}")
                self._append_job_tail(job_id, f"dump output size={size}")
            if timed_out:
                return return_code, f"native dump timeout after {timeout}s"
            return return_code, self._summarize_error(seen_errors, return_code, "dvd_reader_dump")

        if timed_out:
            return return_code, f"native dump timeout after {timeout}s"

        return return_code, None if not seen_errors else self._summarize_error(seen_errors, return_code, "dvd_reader_dump")

    def _run_ffmpeg_attempt(
        self,
        job_id: str,
        command: List[str],
        timeout: int | None = None,
    ) -> Tuple[int | None, str | None]:
        if timeout is None:
            timeout = self.DEFAULT_CMD_TIMEOUT_SECONDS
        seen_errors: List[str] = []
        duration_seconds: float | None = None
        last_error: str | None = None

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

        def on_line(raw: str) -> None:
            if raw is None:
                return
            clean = str(raw).rstrip()
            self._append_job_tail(job_id, clean)

            nonlocal duration_seconds

            if clean.strip():
                lower = clean.lower()
                if len(seen_errors) < self.MAX_ATTEMPT_FAILURE_LINES and any(
                    token in lower for token in ("error", "failed", "invalid", "unable", "cannot")
                ):
                    seen_errors.append(clean)

                if "time=" in clean:
                    match = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", clean)
                    if match:
                        current = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
                        if duration_seconds:
                            current_progress = min(100.0, max(0.0, (current / duration_seconds) * 100.0))
                            self._set_job_state(job_id, progress=current_progress)

                if "Duration:" in clean:
                    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", clean)
                    if match:
                        duration_seconds = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))

        timed_out = self._consume_lines(job_id, proc, proc.stderr, on_line, timeout)
        try:
            return_code = proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._request_cancelled_process(proc)
            return_code = proc.wait(timeout=10)

        self._heartbeat_job(job_id)

        if return_code != 0:
            self._append_job_tail(job_id, f"ffmpeg exited with code {return_code}")
            if timed_out:
                last_error = f"ffmpeg timeout after {timeout}s"
            else:
                last_error = self._summarize_error(seen_errors, return_code, "ffmpeg")
        else:
            last_error = self._summarize_error(seen_errors, return_code, "ffmpeg")

        try:
            if proc.poll() is not None:
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

        return return_code, last_error

    def _run_handbrake_attempt(
        self,
        job_id: str,
        command: List[str],
        timeout: int | None = None,
    ) -> Tuple[int | None, str | None]:
        if timeout is None:
            timeout = self.DEFAULT_CMD_TIMEOUT_SECONDS
        seen_errors: List[str] = []
        return_code: int | None = None

        try:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            raise RuntimeError(f"cannot launch HandBrakeCLI process: {exc}") from exc

        self._set_job_state(job_id, pid=proc.pid)

        def on_line(raw: str) -> None:
            if raw is None:
                return
            clean = str(raw).rstrip()
            self._append_job_tail(job_id, clean)
            if clean.strip():
                lower = clean.lower()
                if len(seen_errors) < self.MAX_ATTEMPT_FAILURE_LINES and any(
                    token in lower for token in ("error", "failed", "error while", "not found", "unable")
                ):
                    seen_errors.append(clean)

        timed_out = self._consume_lines(job_id, proc, proc.stdout, on_line, timeout)
        try:
            return_code = proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._request_cancelled_process(proc)
            return_code = proc.wait(timeout=10)

        self._heartbeat_job(job_id)

        if return_code != 0:
            self._append_job_tail(job_id, f"HandBrakeCLI exited with code {return_code}")
            if timed_out:
                return return_code, f"HandBrakeCLI timeout after {timeout}s"
            return return_code, self._summarize_error(seen_errors, return_code, "handbrake")

        try:
            if proc.poll() is not None:
                proc.terminate()
                proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
        except Exception:
            logging.exception("failed to finalize handbrake process for job=%s", job_id)

        self._set_job_state(job_id, pid=None)

        return return_code, None if return_code == 0 else self._summarize_error(seen_errors, return_code, "handbrake")

    def _derive_retry_attempts(self, command: dict, attempt_error: str, output: str, base_device: str) -> list[dict]:
        lower = (attempt_error or "").lower()
        argv = command.get("argv") or []
        if not isinstance(argv, list) or len(argv) < 2:
            return []

        joined = " ".join(str(item) for item in argv).lower()
        if "/dev/" not in joined and ".vob" not in joined and "video_ts" not in joined and "dvd://" not in joined:
            return []

        source = command.get("input_source") or base_device
        source_path = str(source)
        base_name = Path(str(source_path)).name if source_path else "source"
        retries: list[dict] = []

        input_format = command.get("input_format") or "mpeg"
        if isinstance(input_format, str) and input_format.lower() not in {"mpeg", "dvd", "concat", "handbrake"}:
            input_format = "mpeg"

        def make_candidate(argv_candidate: list[str], *, label_suffix: str) -> dict | None:
            if not argv_candidate:
                return None
            candidate = list(argv_candidate)
            if not candidate or candidate[-1] == output:
                pass
            elif output not in candidate:
                candidate.append(output)
            return {
                "label": f"{label_suffix}",
                "argv": candidate,
                "input_format": input_format,
                "input_source": source_path,
            }

        def add_attempt(label: str, argv_candidate: list[str]) -> None:
            attempt = make_candidate(argv_candidate, label_suffix=label)
            if attempt:
                retries.append(attempt)

        def drop_long_option(values: list[str], option: str) -> list[str]:
            target = option.lower().lstrip("-")
            values = [str(v) for v in values]
            out: list[str] = []
            skip_next = False
            for idx, value in enumerate(values):
                if skip_next:
                    skip_next = False
                    continue

                lower_token = value.lower()
                if lower_token.startswith("--"):
                    key, has_value = (lower_token[2:].split("=", 1) + [""])[:2]
                    if key == target or key == target.replace("-", ""):
                        continue
                    if has_value and key == target:
                        continue

                normalized = lower_token.lstrip("-")
                if normalized == target:
                    skip_next = target in {"f", "i", "title", "dvd_device", "codec", "c", "c:v", "c:a", "map"}
                    continue
                out.append(value)

            return out

        if "unrecognized option" in lower or "option not found" in lower or "option 'title'" in lower:
            add_attempt(
                f"Retry ingénieur: nettoyage options strictes ({base_name})",
                [arg for arg in argv if arg not in {"-ignore_unknown", "-sn", "-dn", "ignore_err", "-copyts"}],
            )

        unknowns = re.findall(r"unrecognized option '([^']+)'", lower)
        for unknown in unknowns:
            if not unknown:
                continue
            cleaned = drop_long_option(argv, unknown)
            if cleaned != argv:
                add_attempt(f"Retry ingénieur: suppression '{unknown}'", cleaned)

            if unknown.lower() == "dvdvideo":
                cleaned_dvd: list[str] = []
                skip_next = False
                for i, value in enumerate(argv):
                    if skip_next:
                        skip_next = False
                        continue
                    if value == "-f" and i + 1 < len(argv) and str(argv[i + 1]).lower() == "dvdvideo":
                        skip_next = True
                        continue
                    if str(value).lower() == "dvdvideo":
                        continue
                    cleaned_dvd.append(value)
                add_attempt("Retry ingénieur: sans format dvdvideo", cleaned_dvd)

            if unknown.lower() == "title":
                add_attempt("Retry ingénieur: suppression -title", [v for i, v in enumerate(argv) if i > 0 and str(argv[i - 1]).lower() != "-title"])

        if "-dvd_device" in argv:
            for idx, arg in enumerate(argv):
                if arg == "-dvd_device" and idx + 1 < len(argv):
                    add_attempt(f"Retry ingénieur: sans -dvd_device ({base_name})", [*argv[:idx], *argv[idx + 2 :]])
                    break

        if "option 'b:a'" in lower and ("cannot be applied" in lower or "invalid argument" in lower):
            repaired: list[str] = []
            skip = False
            i = 0
            while i < len(argv):
                if skip:
                    skip = False
                    i += 1
                    continue
                token = argv[i]
                if token == "-b:a" and i + 1 < len(argv):
                    skip = True
                    i += 1
                    continue
                if token == "-c:a" and i + 1 < len(argv):
                    repaired.extend(["-c:a", "aac"])
                    skip = True
                    i += 1
                    continue
                repaired.append(token)
                i += 1
            add_attempt(
                f"Retry ingénieur: correction bitrate/codec audio ({base_name})",
                [
                    *repaired,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-pix_fmt",
                    "yuv420p",
                    "-ac",
                    "2",
                    "-movflags",
                    "+faststart",
                ],
            )

        if "parser not found for codec none" in lower or "codec none" in lower:
            video_only_base = self._strip_audio_from_args(list(argv))
            if video_only_base != list(argv):
                add_attempt(
                    f"Retry ingénieur: suppression complète de l'audio ({base_name})",
                    self._ensure_video_only_args(video_only_base, output),
                )

        if "invalid data found when processing input" in lower:
            add_attempt(
                f"Retry ingénieur: stratégie vidéo only ({base_name})",
                self._ensure_video_only_args(argv, output),
            )

        if "verify-output-failed" in lower and self.ffmpeg_supports_mpeg and source:
            add_attempt(
                f"Retry ingénieur: relecture permissive ({base_name})",
                [
                    self.ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-nostdin",
                    "-fflags",
                    "+genpts",
                    "-err_detect",
                    "ignore_err",
                    "-analyzeduration",
                    "60M",
                    "-probesize",
                    "60M",
                    *(["-f", "mpeg"] if "/dev/" not in str(source_path).lower() else []),
                    "-i",
                    source_path,
                    "-map",
                    "0:v:0?",
                    "-map",
                    "0:a:0?",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-ac",
                    "2",
                    "-b:a",
                    "192k",
                    "-movflags",
                    "+faststart",
                    "-sn",
                    "-dn",
                ],
            )

        if ("invalid data" in lower or "protocol not found" in lower or "unknown input format" in lower or "permission denied" in lower) and source:
            if self.ffmpeg_supports_mpeg:
                add_attempt(
                    f"Retry ingénieur: entrée mpeg permissive ({base_name})",
                    [
                        self.ffmpeg,
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "warning",
                        "-nostdin",
                        "-fflags",
                        "+genpts",
                        "-err_detect",
                        "ignore_err",
                        "-analyzeduration",
                        "60M",
                        "-probesize",
                        "60M",
                        "-f",
                        "mpeg",
                        "-i",
                        source_path,
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "24",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-ac",
                        "2",
                        "-b:a",
                        "192k",
                        "-movflags",
                        "+faststart",
                        "-map",
                        "0:v:0?",
                        "-map",
                        "0:a:0?",
                    ],
                )

        if ("bad data" in lower or "corrupt" in lower or "permission denied" in lower) and source:
            mount = self._mounted_volume(base_device)
            if mount:
                titles = self._scan_vob_titles(mount / "VIDEO_TS")
                for title_id, parts in titles:
                    retries.extend(self._build_vob_concat_commands(title_id, parts, output, mount))

        normalized: list[tuple[str, ...]] = []
        deduped: list[dict] = []
        for retry in retries:
            key = tuple(str(item) for item in (retry.get("argv") or []))
            if key in normalized:
                continue
            normalized.append(key)
            deduped.append(retry)

        return deduped

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
                return line
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
        if not source:
            return
        if not self._probe_source_access(source)[0]:
            return

        if command_timeout is None:
            command_timeout = self.DEFAULT_CMD_TIMEOUT_SECONDS

        base_input = [
            self.ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-analyzeduration",
            "60M",
            "-probesize",
            "60M",
        ]

        tolerant = ["-fflags", "+genpts", "-err_detect", "ignore_err", "-ignore_unknown"]
        common_map = ["-map", "0:v:0?", "-map", "0:a:0?"]
        mux_out = ["-sn", "-dn", "-movflags", "+faststart"]

        profiles: list[tuple[str, list[str]]] = [
            (
                "transcode",
                [
                    *base_input,
                    *input_args,
                    "-i",
                    source,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-ac",
                    "2",
                    "-b:a",
                    "192k",
                    "-max_muxing_queue_size",
                    "4096",
                    *common_map,
                    *mux_out,
                ],
            ),
            (
                "transcode sans audio",
                [
                    *base_input,
                    *input_args,
                    "-i",
                    source,
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-pix_fmt",
                    "yuv420p",
                    "-max_muxing_queue_size",
                    "4096",
                    "-map",
                    "0:v:0?",
                    *mux_out,
                ],
            ),
            (
                "copy video + AAC",
                [
                    *base_input,
                    *input_args,
                    "-i",
                    source,
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-ac",
                    "2",
                    *common_map,
                    *mux_out,
                ],
            ),
            (
                "copy video sans audio",
                [
                    *base_input,
                    *input_args,
                    "-i",
                    source,
                    "-an",
                    "-c:v",
                    "copy",
                    "-map",
                    "0:v:0?",
                    *mux_out,
                ],
            ),
        ]

        for name, argv in profiles:
            commands.append(
                {
                    "label": f"{label} — {name}",
                    "argv": argv + [output],
                    "input_format": "mpeg",
                    "input_source": source,
                    "timeout": command_timeout,
                }
            )

        if engineer_mode:
            commands.append(
                {
                    "label": f"{label} — tolerant",
                    "argv": [
                        *base_input,
                        *input_args,
                        "-i",
                        source,
                        *tolerant,
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "22",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-ac",
                        "2",
                        "-b:a",
                        "192k",
                        "-max_muxing_queue_size",
                        "4096",
                        *common_map,
                        *mux_out,
                        output,
                    ],
                    "input_format": "mpeg",
                    "input_source": source,
                    "timeout": command_timeout,
                }
            )

    def _build_ffmpeg_commands(self, device: str, output_path: Path, mode: str = "normal") -> list[dict]:
        output = str(output_path)
        base_device = device if device.startswith("/dev/") else f"/dev/{device}"
        alt_device = self._alt_device(base_device)
        mount_points = self._mounted_volume_candidates(base_device)

        source_candidates = [base_device]
        if alt_device:
            source_candidates.append(alt_device)
        source_candidates = list(dict.fromkeys(source_candidates))

        engineer_mode = mode in {"engineer", "advanced"}
        commands: list[dict] = []

        # 1) Priorité: source montée (VIDEO_TS + VOB) quand le disque est bien monté.
        if mount_points:
            for mount_point in mount_points:
                commands.extend(self._build_vob_title_commands(mount_point, output))
                root_vob = mount_point / "VIDEO_TS" / "VIDEO_TS.VOB"
                if root_vob.is_file() and self._probe_source_access(str(root_vob))[0]:
                    self._build_ffmpeg_source_commands(
                        commands,
                        str(root_vob),
                        f"VIDEO_TS.VOB ({mount_point.name})",
                        output,
                        ["-f", "mpeg"],
                        engineer_mode,
                    )

        # 2) Mécanisme d’ingénieur: extraction native (libdvdread)
        if engineer_mode and self.native_dump_available:
            commands.extend(self._build_native_dump_pipelines(source_candidates, output))

        # 3) Accès direct block device (sans heuristique dvd://)
        for source in source_candidates:
            self._build_ffmpeg_source_commands(
                commands,
                source,
                f"Périphérique ({Path(source).name})",
                output,
                [],
                engineer_mode,
            )

        # 4) HandBrakeCLI comme dernier recours (si disponible)
        if engineer_mode and self.handbrake:
            commands.append(
                {
                    "label": "HandBrakeCLI périphérique (transcode)",
                    "argv": [
                        self.handbrake,
                        "-i",
                        base_device,
                        "-o",
                        output,
                        "-e",
                        "x264",
                        "--audio-lang-list",
                        "fra,eng",
                        "--all-audio",
                        "--all-subtitles",
                        "--encoder-preset",
                        "medium",
                        "--aencoder",
                        "ca_aac",
                        "--quality",
                        "22",
                        "--optimize",
                        "--x264-preset",
                        "fast",
                        "-v",
                        "1",
                    ],
                    "input_format": "handbrake",
                    "input_source": base_device,
                    "timeout": self.DEFAULT_CMD_TIMEOUT_SECONDS,
                }
            )
            for mount_point in mount_points:
                source = str(mount_point / "VIDEO_TS")
                commands.append(
                    {
                        "label": f"HandBrakeCLI VIDEO_TS ({mount_point.name})",
                        "argv": [
                            self.handbrake,
                            "-i",
                            source,
                            "-o",
                            output,
                            "--preset",
                            "Fast 1080p30",
                        ],
                        "input_format": "handbrake",
                        "input_source": source,
                        "timeout": self.DEFAULT_CMD_TIMEOUT_SECONDS,
                    }
                )

        # 5) déduplication + garde-fou final
        unique: list[dict] = []
        seen: set[tuple[str, ...]] = set()
        for cmd in commands:
            if self.debug_enabled:
                logging.debug("rip strategy: %s", cmd.get("label"))

            argv = cmd.get("argv")
            if isinstance(argv, list):
                key = tuple(str(item) for item in argv)
            else:
                pipeline = cmd.get("pipeline")
                if not pipeline:
                    continue
                key = tuple(f"pipeline:{str(step)}" for step in pipeline)

            if key in seen:
                continue
            seen.add(key)
            unique.append(cmd)

        if not unique:
            raise RuntimeError("no extraction commands prepared")

        return unique

    def _build_native_dump_pipelines(self, source_candidates: list[str], output: str) -> list[dict]:
        commands: list[dict] = []

        for source in source_candidates:
            if not source:
                continue

            title_candidates: list[NativeTitleProbe] = []
            try:
                title_candidates = list_title_candidates(source)[:3]
            except Exception as exc:
                logging.debug("native title probe failed for %s: %s", source, exc)

            if not title_candidates:
                title_candidates = [NativeTitleProbe(title=1, blocks=0, size_bytes=0)]

            for item in title_candidates[:2]:
                with tempfile.NamedTemporaryFile(
                    prefix=f"dvd_native_{Path(source).name}_t{int(item.title):02d}_",
                    suffix=".vob",
                    delete=False,
                ) as dump_file:
                    dump_path = Path(dump_file.name)

                dump_cmd = dump_command_for_title(source, int(item.title), str(dump_path))

                ffmpeg_cmd = [
                    self.ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-nostdin",
                    "-analyzeduration",
                    "60M",
                    "-probesize",
                    "60M",
                    "-f",
                    "mpeg",
                    "-i",
                    str(dump_path),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-pix_fmt",
                    "yuv420p",
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
                    "-sn",
                    "-dn",
                    output,
                ]

                commands.append(
                    {
                        "label": f"pipeline libdvdread (source={Path(source).name}, title={int(item.title)})",
                        "tool": "pipeline",
                        "pipeline": [
                            {
                                "tool": "dvd_reader_dump",
                                "argv": dump_cmd,
                                "artifacts": [str(dump_path)],
                                "label": f"dvd_reader_dump titre {int(item.title)}",
                            },
                            {
                                "tool": "ffmpeg",
                                "argv": ffmpeg_cmd,
                                "label": f"ffmpeg transcode titre {int(item.title)}",
                                "artifacts": [],
                            },
                        ],
                        "artifacts": [str(dump_path)],
                        "input_format": "native",
                        "input_source": source,
                    }
                )

                if item.title and item.blocks:
                    commands.append(
                        {
                            "label": f"pipeline libdvdread (sans audio) source={Path(source).name}, title={int(item.title)}",
                            "tool": "pipeline",
                            "pipeline": [
                                {
                                    "tool": "dvd_reader_dump",
                                    "argv": dump_cmd,
                                    "artifacts": [str(dump_path)],
                                    "label": f"dvd_reader_dump titre {int(item.title)}",
                                },
                                {
                                    "tool": "ffmpeg",
                                    "argv": [
                                        self.ffmpeg,
                                        "-y",
                                        "-hide_banner",
                                        "-loglevel",
                                        "warning",
                                        "-nostdin",
                                        "-analyzeduration",
                                        "60M",
                                        "-probesize",
                                        "60M",
                                        "-f",
                                        "mpeg",
                                        "-i",
                                        str(dump_path),
                                        "-an",
                                        "-c:v",
                                        "libx264",
                                        "-preset",
                                        "veryfast",
                                        "-crf",
                                        "22",
                                        "-pix_fmt",
                                        "yuv420p",
                                        "-movflags",
                                        "+faststart",
                                        "-map",
                                        "0:v:0?",
                                        "-sn",
                                        "-dn",
                                        output,
                                    ],
                                    "label": f"ffmpeg sans audio titre {int(item.title)}",
                                    "artifacts": [],
                                },
                            ],
                            "artifacts": [str(dump_path)],
                            "input_format": "native",
                            "input_source": source,
                        }
                    )

        # remove duplicates preserving order
        deduped: list[dict] = []
        deduped_signatures: set[tuple[str, ...]] = set()
        for cmd in commands:
            signature = tuple(str(item) for item in (cmd.get("pipeline") or []))
            if signature in deduped_signatures:
                continue
            deduped_signatures.add(signature)
            deduped.append(cmd)

        return deduped

    def _mounted_volume(self, device: str) -> Optional[Path]:
        mounts = self._mounted_volume_candidates(device)
        return mounts[0] if mounts else None

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
        if sample and isinstance(sample.get("bytes"), int):
            ok = sample.get("bytes", 0) > 0
            info = (ok, None if ok else "no bytes")
            self._source_probe_cache[source] = info
            if ok:
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
        video_ts = mount_point / "VIDEO_TS"
        if not video_ts.is_dir():
            return []

        titles = self._scan_vob_titles(video_ts)
        commands: list[dict] = []
        tolerant_opts = ["-fflags", "+genpts", "-err_detect", "ignore_err", "-ignore_unknown", "-err_detect", "ignore_err"]

        for title_id, parts in titles:
            if len(parts) == 1:
                source = parts[0]
                commands.append(
                    {
                        "label": f"VOB titre VTS_{title_id:02d} direct transcode ({mount_point.name})",
                        "argv": [
                            self.ffmpeg,
                            "-y",
                            "-hide_banner",
                            "-loglevel",
                            "warning",
                            "-f",
                            "mpeg",
                            "-i",
                            str(source),
                            "-c:v",
                            "libx264",
                            "-preset",
                            "veryfast",
                            "-crf",
                            "22",
                            "-pix_fmt",
                            "yuv420p",
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
                            "-sn",
                            "-dn",
                            output,
                        ],
                        "artifacts": [],
                        "input_format": "mpeg",
                        "input_source": str(source),
                    }
                )
                commands.append(
                    {
                        "label": f"VOB titre VTS_{title_id:02d} direct transcode sans audio ({mount_point.name})",
                        "argv": [
                            self.ffmpeg,
                            "-y",
                            "-hide_banner",
                            "-loglevel",
                            "warning",
                            "-f",
                            "mpeg",
                            "-i",
                            str(source),
                            "-c:v",
                            "libx264",
                            "-preset",
                            "veryfast",
                            "-crf",
                            "22",
                            "-pix_fmt",
                            "yuv420p",
                            "-an",
                            "-movflags",
                            "+faststart",
                            "-map",
                            "0:v:0?",
                            "-sn",
                            "-dn",
                            output,
                        ],
                        "artifacts": [],
                        "input_format": "mpeg",
                        "input_source": str(source),
                    }
                )
                commands.append(
                    {
                        "label": f"VOB titre VTS_{title_id:02d} direct transcode tolerant ({mount_point.name})",
                        "argv": [
                            self.ffmpeg,
                            "-y",
                            "-hide_banner",
                            "-loglevel",
                            "warning",
                            "-analyzeduration",
                            "60M",
                            "-probesize",
                            "60M",
                            *tolerant_opts,
                            "-f",
                            "mpeg",
                            "-i",
                            str(source),
                            "-c:v",
                            "libx264",
                            "-preset",
                            "veryfast",
                            "-crf",
                            "22",
                            "-pix_fmt",
                            "yuv420p",
                            "-c:a",
                            "aac",
                            "-b:a",
                            "192k",
                            "-ac",
                            "2",
                            "-movflags",
                            "+faststart",
                            "-map",
                            "0:v:0?",
                            "-map",
                            "0:a:0?",
                            "-sn",
                            "-dn",
                            output,
                        ],
                        "artifacts": [],
                        "input_format": "mpeg",
                        "input_source": str(source),
                    }
                )
                commands.append(
                    {
                        "label": f"VOB titre VTS_{title_id:02d} direct copy ({mount_point.name})",
                        "argv": [
                            self.ffmpeg,
                            "-y",
                            "-hide_banner",
                            "-loglevel",
                            "warning",
                            "-f",
                            "mpeg",
                            "-i",
                            str(source),
                            "-c:v",
                            "copy",
                            "-c:a",
                            "copy",
                            "-map",
                            "0:v:0?",
                            "-map",
                            "0:a:0?",
                            "-sn",
                            "-dn",
                            output,
                        ],
                        "artifacts": [],
                        "input_format": "mpeg",
                        "input_source": str(source),
                    }
                )
                commands.append(
                    {
                        "label": f"VOB titre VTS_{title_id:02d} direct copy sans audio ({mount_point.name})",
                        "argv": [
                            self.ffmpeg,
                            "-y",
                            "-hide_banner",
                            "-loglevel",
                            "warning",
                            "-f",
                            "mpeg",
                            "-i",
                            str(source),
                            "-an",
                            "-c:v",
                            "copy",
                            "-map",
                            "0:v:0?",
                            "-sn",
                            "-dn",
                            output,
                        ],
                        "artifacts": [],
                        "input_format": "mpeg",
                        "input_source": str(source),
                    }
                )
                commands.append(
                    {
                        "label": f"VOB titre VTS_{title_id:02d} direct copy tolerant ({mount_point.name})",
                        "argv": [
                            self.ffmpeg,
                            "-y",
                            "-hide_banner",
                            "-loglevel",
                            "warning",
                            "-analyzeduration",
                            "60M",
                            "-probesize",
                            "60M",
                            *tolerant_opts,
                            "-f",
                            "mpeg",
                            "-i",
                            str(source),
                            "-c",
                            "copy",
                            "-map",
                            "0:v:0?",
                            "-map",
                            "0:a:0?",
                            "-sn",
                            "-dn",
                            output,
                        ],
                        "artifacts": [],
                        "input_format": "mpeg",
                        "input_source": str(source),
                    }
                )
            else:
                commands.extend(self._build_vob_concat_commands(title_id, parts, output, mount_point))

        return commands

    def _scan_vob_titles(self, video_ts: Path) -> list[tuple[int, list[Path]]]:
        titles = scan_vob_titles_from_video_ts(video_ts)
        if titles:
            return titles[:6]

        vob_files = sorted(video_ts.glob("VTS_*_*.VOB"))
        if not vob_files:
            return []

        title_parts: dict[int, list[Path]] = {}
        for file in vob_files:
            match = re.match(r"VTS_(\d{1,2})_(\d{1,2})\.VOB$", file.name, re.IGNORECASE)
            if not match:
                continue
            title_id = int(match.group(1))
            part_no = int(match.group(2))
            if part_no == 0:
                continue
            title_parts.setdefault(title_id, []).append(file)

        candidates: list[tuple[int, int, int, list[Path]]] = []
        for title_id, parts in title_parts.items():
            if not parts:
                continue
            sorted_parts = sorted(parts, key=lambda item: item.name)
            readable_parts = [part for part in sorted_parts if self._probe_source_access(str(part))[0]]
            if not readable_parts:
                logging.debug("ignore title %s: no readable vob parts", title_id)
                continue

            video_parts = [part for part in readable_parts if self._vob_has_video(part)]
            if not video_parts:
                logging.debug("ignore title %s: no video stream detected", title_id)
                continue

            total_size = sum(self._safe_file_size(path) for path in sorted_parts)
            candidates.append((title_id, total_size, len(video_parts), video_parts))

        candidates.sort(key=lambda item: (-item[1], -item[2], item[0]))
        return [(item[0], item[3]) for item in candidates[:6]]

    @staticmethod
    def _vob_duration(file: Path) -> float:
        result = run_cmd(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(file)], timeout=8)
        if result.return_code != 0:
            return 0.0
        text = (result.stdout or "").strip().splitlines()[0] if result.stdout else ""
        try:
            return float(text)
        except Exception:
            return 0.0

    @staticmethod
    def _vob_has_video(file: Path) -> bool:
        if not file.exists() or not file.is_file():
            return False
        result = run_cmd(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=index", "-of", "default=noprint_wrappers=1:nokey=1", str(file)], timeout=8)
        if result.return_code != 0:
            return False
        return bool((result.stdout or "").strip())

    def _build_vob_concat_commands(self, title_id: int, parts: list[Path], output: str, mount_point: Optional[Path] = None) -> list[dict]:
        if len(parts) <= 1:
            return []

        part_list = sorted(parts, key=lambda item: item.name)
        if not part_list:
            return []

        def make_list_file() -> str | None:
            tmp_fd, tmp_name = tempfile.mkstemp(prefix=f"dvdvob_{title_id}_", suffix=".txt")
            tmp_path = Path(tmp_name)

            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
                    for part in part_list:
                        handle.write(f"file '{part.as_posix()}'\n")
                return str(tmp_path)
            except Exception as exc:
                logging.warning("unable to build concat list for title %s: %s", title_id, exc)
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
                return None

        suffix = f" ({mount_point.name})" if mount_point else ""
        commands: list[dict] = []

        transcode_list = make_list_file()
        if transcode_list:
            input_opts = ["-f", "concat", "-safe", "0", "-i", transcode_list]
            commands.append(
                {
                    "label": f"Concat VOB titre VTS_{title_id:02d}{suffix} (transcode)",
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
                        "veryfast",
                        "-crf",
                        "22",
                        "-pix_fmt",
                        "yuv420p",
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
                        "-sn",
                        "-dn",
                        output,
                    ],
                    "artifacts": [transcode_list],
                    "input_format": "concat",
                    "input_source": transcode_list,
                }
            )
            commands.append(
                {
                    "label": f"Concat VOB titre VTS_{title_id:02d}{suffix} (transcode sans audio)",
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
                        "veryfast",
                        "-crf",
                        "22",
                        "-pix_fmt",
                        "yuv420p",
                        "-an",
                        "-movflags",
                        "+faststart",
                        "-map",
                        "0:v:0?",
                        "-sn",
                        "-dn",
                        output,
                    ],
                    "artifacts": [transcode_list],
                    "input_format": "concat",
                    "input_source": transcode_list,
                }
            )
            commands.append(
                {
                    "label": f"Concat VOB titre VTS_{title_id:02d}{suffix} (transcode tolerant)",
                    "argv": [
                        self.ffmpeg,
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "warning",
                        "-analyzeduration",
                        "60M",
                        "-probesize",
                        "60M",
                        "-fflags",
                        "+genpts",
                        "-err_detect",
                        "ignore_err",
                        "-ignore_unknown",
                        *input_opts,
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "22",
                        "-pix_fmt",
                        "yuv420p",
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
                        "-sn",
                        "-dn",
                        output,
                    ],
                    "artifacts": [transcode_list],
                    "input_format": "concat",
                    "input_source": transcode_list,
                }
            )

        copy_list = make_list_file()
        if copy_list:
            input_opts = ["-f", "concat", "-safe", "0", "-i", copy_list]
            commands.append(
                {
                    "label": f"Concat VOB titre VTS_{title_id:02d}{suffix} (copy)",
                    "argv": [
                        self.ffmpeg,
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "warning",
                        *input_opts,
                        "-c:v",
                        "copy",
                        "-c:a",
                        "copy",
                        "-map",
                        "0:v:0?",
                        "-map",
                        "0:a:0?",
                        "-sn",
                        "-dn",
                        output,
                    ],
                    "artifacts": [copy_list],
                    "input_format": "concat",
                    "input_source": copy_list,
                }
            )
            commands.append(
                {
                    "label": f"Concat VOB titre VTS_{title_id:02d}{suffix} (copy sans audio)",
                    "argv": [
                        self.ffmpeg,
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "warning",
                        *input_opts,
                        "-an",
                        "-c:v",
                        "copy",
                        "-map",
                        "0:v:0?",
                        "-sn",
                        "-dn",
                        output,
                    ],
                    "artifacts": [copy_list],
                    "input_format": "concat",
                    "input_source": copy_list,
                }
            )
            commands.append(
                {
                    "label": f"Concat VOB titre VTS_{title_id:02d}{suffix} (copy tolerant)",
                    "argv": [
                        self.ffmpeg,
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "warning",
                        "-analyzeduration",
                        "60M",
                        "-probesize",
                        "60M",
                        "-fflags",
                        "+genpts",
                        "-err_detect",
                        "ignore_err",
                        "-ignore_unknown",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        copy_list,
                        "-c",
                        "copy",
                        "-map",
                        "0:v:0?",
                        "-map",
                        "0:a:0?",
                        "-sn",
                        "-dn",
                        output,
                    ],
                    "artifacts": [copy_list],
                    "input_format": "concat",
                    "input_source": copy_list,
                }
            )

        return commands

    @staticmethod
    def _safe_file_size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

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

            if token == "-map" and index + 1 < len(cleaned) and str(cleaned[index + 1]).startswith("0:a:"):
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

            if token == "-map" and index + 1 < len(argv) and str(argv[index + 1]).startswith("0:a:"):
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
