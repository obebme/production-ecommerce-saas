"""Disposable, Windows-safe PostgreSQL test runner for backend pytest suites.

Invokes PostgreSQL binaries directly inside a temporary directory under %TEMP%
without inheriting open subprocess.PIPE handles on daemon startup, provisions a
least-privilege test role, sets TEST_DATABASE_URL in the child pytest environment
only, and guarantees complete cluster teardown and post-cleanup PID verification.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote_plus

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PG_BIN = r"E:\databasesql\bin"
FORBIDDEN_DATA_DIR = Path(r"E:\databasesql\data")
TEST_ROLE_NAME = "ecom_test_role"
TEST_DB_NAME = "ecom_test_db"

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87


def _log(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    print(f"[pg-runner] {message}", file=stream, flush=True)


def _sanitized_pg_env() -> dict[str, str]:
    """Return process environment stripped of ambient PG* overrides."""
    return {k: v for k, v in os.environ.items() if not k.upper().startswith("PG")}


def _allocate_free_loopback_port() -> int:
    """Bind an ephemeral loopback socket on 127.0.0.1 and return an unused non-5432 port."""
    for _ in range(10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port > 0 and port != 5432:
            return port
    raise RuntimeError("Unable to allocate an ephemeral non-5432 loopback port on 127.0.0.1.")


def _validate_temp_cluster_dir(cluster_dir: Path) -> None:
    """Ensure the disposable cluster directory resides strictly inside %TEMP%."""
    resolved_cluster = cluster_dir.resolve()
    resolved_temp = Path(tempfile.gettempdir()).resolve()
    if not resolved_cluster.is_relative_to(resolved_temp):
        raise RuntimeError(
            f"Refusing non-temporary cluster directory '{resolved_cluster}' "
            f"(must be under '{resolved_temp}')."
        )
    resolved_forbidden = FORBIDDEN_DATA_DIR.resolve()
    if resolved_cluster == resolved_forbidden or resolved_cluster.is_relative_to(
        resolved_forbidden
    ):
        raise RuntimeError(f"Refusing protected PostgreSQL data directory '{resolved_cluster}'.")


def _resolve_pg_binaries(pg_bin_raw: str) -> dict[str, Path]:
    pg_bin = Path(pg_bin_raw).expanduser().resolve()
    if not pg_bin.is_dir():
        raise FileNotFoundError(f"PostgreSQL bin directory not found: '{pg_bin}'.")

    suffix = ".exe" if os.name == "nt" else ""
    binaries: dict[str, Path] = {}
    for tool in ("initdb", "pg_ctl", "pg_isready", "psql"):
        candidate = pg_bin / f"{tool}{suffix}"
        if not candidate.is_file():
            raise FileNotFoundError(f"Required PostgreSQL executable missing: '{candidate}'.")
        binaries[tool] = candidate
    return binaries


def _terminate_process_tree(proc: subprocess.Popen[bytes] | subprocess.Popen[str]) -> None:
    """Terminate an active child process cleanly, escalating to force kill if needed."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            proc.wait(timeout=5)
            return
        except (OSError, subprocess.SubprocessError):
            pass

    try:
        proc.terminate()
        proc.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _is_pid_running(pid: int) -> bool:
    """Return True if the OS reports the specified process ID is still alive."""
    if pid <= 0:
        return False

    if os.name == "nt":
        win_dll_cls = getattr(ctypes, "WinDLL", None)
        if win_dll_cls is not None:
            kernel32 = win_dll_cls("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                try:
                    exit_code = ctypes.c_ulong()
                    if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                        return int(exit_code.value) == _STILL_ACTIVE
                finally:
                    kernel32.CloseHandle(handle)
            else:
                last_error = int(ctypes.get_last_error())
                if last_error == _ERROR_INVALID_PARAMETER:
                    return False

        probe = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        return re.search(rf"(?<!\d){pid}(?!\d)", probe.stdout) is not None

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _force_kill_temp_pid(pid: int) -> None:
    """Force-terminate ONLY the verified temporary PostgreSQL postmaster PID."""
    if not _is_pid_running(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    else:
        try:
            sig_kill = int(getattr(signal, "SIGKILL", signal.SIGTERM))
            os.kill(pid, sig_kill)
        except ProcessLookupError:
            return


def _read_postmaster_pid(data_dir: Path) -> int | None:
    pid_file = data_dir / "postmaster.pid"
    if not pid_file.is_file():
        return None
    lines = pid_file.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return None
    try:
        pid = int(lines[0].strip())
    except ValueError:
        return None
    return pid if pid > 0 else None


def _run_logged_tool_simple(
    cmd: list[str],
    *,
    log_path: Path,
    timeout_seconds: float,
    env: dict[str, str],
) -> int:
    """Run a cleanup/non-watched tool redirecting stdout/stderr to a log file (no PIPE)."""
    with log_path.open("wb") as log_fh:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            close_fds=True,
            env=env,
            timeout=timeout_seconds,
            check=False,
        )
    return int(proc.returncode)


def _redact_secrets(text: str, secrets_to_scrub: Sequence[str]) -> str:
    scrubbed = text
    for secret in secrets_to_scrub:
        if secret:
            scrubbed = scrubbed.replace(secret, "[REDACTED]")
    return scrubbed


def _preserve_and_print_logs(
    *,
    source_logs: Sequence[Path],
    diag_dir: Path | None,
    secrets_to_scrub: Sequence[str],
) -> None:
    time_tag = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target_dir = (
        diag_dir.expanduser().resolve()
        if diag_dir is not None
        else Path(tempfile.gettempdir()).resolve() / f"ecom_pg_diag_{time_tag}"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    preserved: list[Path] = []

    for src in source_logs:
        if not src.is_file():
            continue
        raw_content = src.read_text(encoding="utf-8", errors="replace")
        safe_content = _redact_secrets(raw_content, secrets_to_scrub)
        dest = target_dir / src.name
        dest.write_text(safe_content, encoding="utf-8")
        preserved.append(dest)

    _log(f"Preserved diagnostic logs in: {target_dir}", error=True)
    for dest in preserved:
        if dest.name in ("postgres.log", "pg_ctl_start.log", "initdb.log"):
            lines = dest.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = "\n".join(lines[-40:]) if lines else "(empty)"
            _log(f"--- tail of {dest.name} ---\n{tail}", error=True)


def _probe_cluster_readiness(
    *,
    pg_isready_exe: Path,
    psql_exe: Path,
    port: int,
    pg_env: dict[str, str],
    watchdog_fired: threading.Event,
    deadline_seconds: float = 20.0,
) -> str:
    """Wait for TCP readiness via pg_isready and verify SQL via SELECT version()."""
    deadline = time.monotonic() + deadline_seconds
    last_error = "readiness probe did not complete"

    while True:
        if watchdog_fired.is_set():
            raise TimeoutError("Overall watchdog fired during cluster readiness check.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"PostgreSQL cluster on 127.0.0.1:{port} did not become ready within "
                f"{deadline_seconds:.0f}s ({last_error})."
            )

        probe_timeout = min(5.0, max(0.5, remaining))
        try:
            ready_res = subprocess.run(
                [
                    str(pg_isready_exe),
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(port),
                    "-U",
                    "postgres",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                env=pg_env,
                timeout=probe_timeout,
                check=False,
            )
            if ready_res.returncode == 0:
                rem_after_ready = deadline - time.monotonic()
                if rem_after_ready <= 0:
                    raise TimeoutError("Readiness deadline reached before SQL version probe.")
                sql_res = subprocess.run(
                    [
                        str(psql_exe),
                        "-h",
                        "127.0.0.1",
                        "-p",
                        str(port),
                        "-U",
                        "postgres",
                        "-d",
                        "postgres",
                        "-v",
                        "ON_ERROR_STOP=1",
                        "-tAc",
                        "SELECT version();",
                    ],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    close_fds=True,
                    env=pg_env,
                    timeout=min(5.0, max(0.5, rem_after_ready)),
                    check=False,
                )
                if sql_res.returncode == 0 and sql_res.stdout.strip():
                    return sql_res.stdout.strip().splitlines()[0].strip()
                last_error = sql_res.stderr.strip() or f"psql exit {sql_res.returncode}"
            else:
                last_error = f"pg_isready exit {ready_res.returncode}"
        except subprocess.TimeoutExpired:
            last_error = "probe timed out"

        sleep_for = min(0.25, max(0.0, deadline - time.monotonic()))
        if sleep_for > 0:
            time.sleep(sleep_for)


def _provision_least_privilege_db(
    *,
    psql_exe: Path,
    port: int,
    role_password: str,
    pg_env: dict[str, str],
) -> None:
    """Create least-privilege ecom_test_role and ecom_test_db via psql stdin only."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", role_password):
        raise ValueError("Generated role password contains unexpected characters.")

    setup_sql = (
        f"CREATE ROLE {TEST_ROLE_NAME} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
        f"NOINHERIT NOREPLICATION PASSWORD '{role_password}';\n"
        f"CREATE DATABASE {TEST_DB_NAME} OWNER {TEST_ROLE_NAME} ENCODING 'UTF8';\n"
        f"\\connect {TEST_DB_NAME}\n"
        f"GRANT ALL ON SCHEMA public TO {TEST_ROLE_NAME};\n"
    )
    res = subprocess.run(
        [
            str(psql_exe),
            "-h",
            "127.0.0.1",
            "-p",
            str(port),
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=setup_sql,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        close_fds=True,
        env=pg_env,
        timeout=15,
        check=False,
    )
    if res.returncode != 0:
        safe_err = _redact_secrets(res.stderr.strip(), [role_password])
        raise RuntimeError(f"psql setup failed (exit {res.returncode}): {safe_err}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run pytest against an isolated, disposable local PostgreSQL cluster.",
    )
    parser.add_argument(
        "--pg-bin",
        default=os.environ.get("PG_BIN_DIR") or DEFAULT_PG_BIN,
        help="Path to PostgreSQL bin directory (default: PG_BIN_DIR or E:\\databasesql\\bin).",
    )
    parser.add_argument(
        "--pytest-timeout",
        type=float,
        default=180.0,
        help="Timeout in seconds for pytest execution (default: 180).",
    )
    parser.add_argument(
        "--overall-timeout",
        type=float,
        default=240.0,
        help="Overall execution watchdog timeout in seconds (default: 240).",
    )
    parser.add_argument(
        "--diag-dir",
        default=None,
        help="Directory to preserve PostgreSQL logs on failure (default: temp dir).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Provision disposable PostgreSQL, run pytest with TEST_DATABASE_URL, and clean up."""
    parser = _build_parser()
    args, extra_args = parser.parse_known_args(list(argv) if argv is not None else None)
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]
    pytest_args = extra_args if extra_args else ["-v"]

    if args.pytest_timeout <= 0 or args.overall_timeout <= 0:
        _log("ERROR: --pytest-timeout and --overall-timeout must be positive.", error=True)
        return 2

    try:
        binaries = _resolve_pg_binaries(args.pg_bin)
        port = _allocate_free_loopback_port()
    except (OSError, RuntimeError, FileNotFoundError) as exc:
        _log(f"ERROR: {exc}", error=True)
        return 1

    cluster_dir = Path(tempfile.mkdtemp(prefix="ecom_pg18_")).resolve()
    _validate_temp_cluster_dir(cluster_dir)
    data_dir = cluster_dir / "data"
    initdb_log = cluster_dir / "initdb.log"
    pg_ctl_start_log = cluster_dir / "pg_ctl_start.log"
    pg_ctl_stop_log = cluster_dir / "pg_ctl_stop.log"
    postgres_log = cluster_dir / "postgres.log"

    pg_env = _sanitized_pg_env()
    role_password = secrets.token_urlsafe(24)
    test_db_url = (
        f"postgresql+psycopg://{TEST_ROLE_NAME}:{quote_plus(role_password)}"
        f"@127.0.0.1:{port}/{TEST_DB_NAME}"
    )
    secrets_to_scrub = (role_password, quote_plus(role_password), test_db_url)

    postmaster_pid: int | None = None
    cluster_started = False
    exit_code = 1

    proc_lock = threading.Lock()
    active_proc: subprocess.Popen[bytes] | None = None
    watchdog_fired = threading.Event()

    def _on_overall_timeout() -> None:
        watchdog_fired.set()
        _log(
            f"WATCHDOG TIMEOUT ({args.overall_timeout:.0f}s) exceeded; aborting active step and "
            "tearing down temporary PostgreSQL cluster.",
            error=True,
        )
        with proc_lock:
            if active_proc is not None:
                _terminate_process_tree(active_proc)

    def _run_watched_logged(
        cmd: list[str],
        *,
        log_path: Path,
        timeout_seconds: float,
        stage_name: str,
    ) -> int:
        nonlocal active_proc
        with log_path.open("wb") as log_fh:
            with proc_lock:
                if watchdog_fired.is_set():
                    return 124
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                    env=pg_env,
                )
                active_proc = proc

            deadline = time.monotonic() + timeout_seconds
            try:
                while True:
                    rc = proc.poll()
                    if rc is not None:
                        return int(rc)
                    if watchdog_fired.is_set():
                        _terminate_process_tree(proc)
                        return 124
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        _terminate_process_tree(proc)
                        raise TimeoutError(
                            f"{stage_name} exceeded timeout ({timeout_seconds:.0f}s)."
                        )
                    time.sleep(min(0.2, remaining))
            finally:
                with proc_lock:
                    if active_proc is proc:
                        active_proc = None

    watchdog = threading.Timer(args.overall_timeout, _on_overall_timeout)
    watchdog.daemon = True
    watchdog.start()

    try:
        # 1. initdb
        _log(f"initdb in temporary cluster dir '{cluster_dir}' (port {port})")
        init_rc = _run_watched_logged(
            [
                str(binaries["initdb"]),
                "-D",
                str(data_dir),
                "-U",
                "postgres",
                "-A",
                "trust",
                "-E",
                "UTF8",
                "--locale=C",
            ],
            log_path=initdb_log,
            timeout_seconds=30.0,
            stage_name="initdb.exe",
        )
        if watchdog_fired.is_set():
            exit_code = 124
        elif init_rc != 0:
            raise RuntimeError(f"initdb.exe exited with status {init_rc}.")
        else:
            # 2. start cluster (no PIPE inherited by postgres.exe)
            _log(f"starting cluster on 127.0.0.1:{port}")
            start_rc = _run_watched_logged(
                [
                    str(binaries["pg_ctl"]),
                    "-D",
                    str(data_dir),
                    "-l",
                    str(postgres_log),
                    "-o",
                    f"-p {port} -h 127.0.0.1",
                    "start",
                    "-w",
                    "-t",
                    "30",
                ],
                log_path=pg_ctl_start_log,
                timeout_seconds=30.0,
                stage_name="pg_ctl.exe start",
            )
            postmaster_pid = _read_postmaster_pid(data_dir)
            if watchdog_fired.is_set():
                exit_code = 124
            elif start_rc != 0:
                raise RuntimeError(f"pg_ctl.exe start exited with status {start_rc}.")
            else:
                cluster_started = True

                # 3. probe readiness
                _log(f"probing readiness on 127.0.0.1:{port} (pid={postmaster_pid})")
                pg_version = _probe_cluster_readiness(
                    pg_isready_exe=binaries["pg_isready"],
                    psql_exe=binaries["psql"],
                    port=port,
                    pg_env=pg_env,
                    watchdog_fired=watchdog_fired,
                    deadline_seconds=20.0,
                )
                _log(f"cluster ready: {pg_version}")

                # 4. provision least-privilege ecom_test_role & ecom_test_db
                _log(f"provisioning {TEST_ROLE_NAME} and {TEST_DB_NAME}")
                _provision_least_privilege_db(
                    psql_exe=binaries["psql"],
                    port=port,
                    role_password=role_password,
                    pg_env=pg_env,
                )

                # 5. run pytest with TEST_DATABASE_URL in subprocess env only
                _log(
                    f"running pytest ({' '.join(pytest_args)}) "
                    f"[timeout={args.pytest_timeout:.0f}s]"
                )
                child_env = os.environ.copy()
                child_env["TEST_DATABASE_URL"] = test_db_url
                child_env["PYTHONUNBUFFERED"] = "1"

                with proc_lock:
                    if watchdog_fired.is_set():
                        exit_code = 124
                        pytest_proc: subprocess.Popen[bytes] | None = None
                    else:
                        pytest_proc = subprocess.Popen(
                            [sys.executable, "-m", "pytest", *pytest_args],
                            cwd=str(BACKEND_DIR),
                            env=child_env,
                            stdin=subprocess.DEVNULL,
                            close_fds=True,
                        )
                        active_proc = pytest_proc

                if pytest_proc is not None:
                    pytest_deadline = time.monotonic() + float(args.pytest_timeout)
                    while True:
                        rc = pytest_proc.poll()
                        if rc is not None:
                            exit_code = int(rc)
                            break
                        if watchdog_fired.is_set():
                            _terminate_process_tree(pytest_proc)
                            exit_code = 124
                            break
                        remaining = pytest_deadline - time.monotonic()
                        if remaining <= 0:
                            _log(
                                f"ERROR: pytest exceeded --pytest-timeout "
                                f"({args.pytest_timeout:.0f}s); terminating.",
                                error=True,
                            )
                            _terminate_process_tree(pytest_proc)
                            exit_code = 124
                            break
                        time.sleep(min(0.25, remaining))

                    with proc_lock:
                        if active_proc is pytest_proc:
                            active_proc = None

                if watchdog_fired.is_set():
                    exit_code = 124
                _log(f"pytest finished with exit code {exit_code}")

    except (OSError, RuntimeError, TimeoutError, ValueError, subprocess.SubprocessError) as exc:
        _log(f"ERROR: {_redact_secrets(str(exc), secrets_to_scrub)}", error=True)
        exit_code = 124 if watchdog_fired.is_set() else 1

    finally:
        watchdog.cancel()
        if postmaster_pid is None and data_dir.is_dir():
            postmaster_pid = _read_postmaster_pid(data_dir)

        if cluster_started or postmaster_pid is not None:
            _log(f"stopping cluster in '{data_dir}' (pid={postmaster_pid})")
            try:
                stop_rc = _run_logged_tool_simple(
                    [
                        str(binaries["pg_ctl"]),
                        "-D",
                        str(data_dir),
                        "-m",
                        "immediate",
                        "-w",
                        "stop",
                    ],
                    log_path=pg_ctl_stop_log,
                    timeout_seconds=20.0,
                    env=pg_env,
                )
                if stop_rc != 0:
                    _log(f"WARNING: pg_ctl stop returned {stop_rc}", error=True)
            except (OSError, subprocess.TimeoutExpired) as exc:
                _log(f"WARNING: pg_ctl stop failed/timed out ({exc})", error=True)

        _log(f"verifying PID exited (pid={postmaster_pid})")
        if postmaster_pid is not None:
            if _is_pid_running(postmaster_pid):
                _log(
                    f"WARNING: temp PID {postmaster_pid} still running after stop; forcing exit.",
                    error=True,
                )
                _force_kill_temp_pid(postmaster_pid)
                time.sleep(0.25)
            if _is_pid_running(postmaster_pid):
                _log(
                    f"ERROR: temp PID {postmaster_pid} remained alive after forced termination!",
                    error=True,
                )
                if exit_code == 0:
                    exit_code = 1
            else:
                _log(
                    f"Temp PID {postmaster_pid} exited; "
                    "zero temporary PostgreSQL processes remain."
                )
        else:
            _log("No temporary postmaster PID started; zero temporary PostgreSQL processes remain.")

        if exit_code != 0 or watchdog_fired.is_set():
            _preserve_and_print_logs(
                source_logs=[postgres_log, pg_ctl_start_log, pg_ctl_stop_log, initdb_log],
                diag_dir=Path(args.diag_dir) if args.diag_dir else None,
                secrets_to_scrub=secrets_to_scrub,
            )

        _log(f"cleaning tempdir '{cluster_dir}'")
        shutil.rmtree(cluster_dir, ignore_errors=True)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
