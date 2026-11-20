"""Windows-safe live full-stack Playwright E2E runner.

Provisions an isolated disposable PostgreSQL cluster inside %TEMP%, runs Alembic
migrations and demo catalog/user seeding, launches FastAPI (uvicorn) on
127.0.0.1:8000 and prebuilt Next.js on 127.0.0.1:3000 without inherited
subprocess.PIPE deadlocks, executes Playwright Chromium specs against
http://localhost:3000, and guarantees full process-tree teardown and PID exit
verification.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
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
import urllib.error
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import IO
from urllib.parse import quote_plus

E2E_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = E2E_DIR.parent
REPO_ROOT = FRONTEND_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"

DEFAULT_PG_BIN = r"E:\databasesql\bin"
FORBIDDEN_DATA_DIR = Path(r"E:\databasesql\data")
DEFAULT_NODE_EXE = Path(r"C:\Program Files\nodejs\node.exe")
DEFAULT_NPX_CMD = Path(r"C:\Program Files\nodejs\npx.cmd")

TEST_ROLE_NAME = "ecom_test_role"
TEST_DB_NAME = "ecom_test_db"
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8000
FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT = 3000
E2E_BASE_URL = f"http://localhost:{FRONTEND_PORT}"
PUBLIC_API_BASE_URL = f"http://localhost:{BACKEND_PORT}/api/v1"
INTERNAL_API_BASE_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}/api/v1"

DEMO_ADMIN_EMAIL = "admin@atelier.example"
DEMO_STAFF_EMAIL = "staff@atelier.example"
DEMO_CUSTOMER_EMAIL = "customer@atelier.example"

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87

_STRIPPED_ENV_PREFIXES = ("PG",)
_STRIPPED_ENV_KEYS = {
    "DATABASE_URL",
    "TEST_DATABASE_URL",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "CSRF_HMAC_SECRET",
    "GUEST_ORDER_HMAC_SECRET",
    "PAYMENT_WEBHOOK_SECRET",
}


def _log(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    print(f"[e2e-runner] {message}", file=stream, flush=True)


def _sanitized_base_env() -> dict[str, str]:
    """Return process environment stripped of ambient PG* and DB/secret overrides."""
    return {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(_STRIPPED_ENV_PREFIXES)
        and k.upper() not in _STRIPPED_ENV_KEYS
    }


def _validate_temp_run_dir(run_dir: Path) -> None:
    """Ensure the disposable run directory resides strictly inside %TEMP%."""
    resolved_dir = run_dir.resolve()
    resolved_temp = Path(tempfile.gettempdir()).resolve()
    if not resolved_dir.is_relative_to(resolved_temp):
        raise RuntimeError(
            f"Refusing non-temporary run directory '{resolved_dir}' "
            f"(must be under '{resolved_temp}')."
        )
    resolved_forbidden = FORBIDDEN_DATA_DIR.resolve()
    if resolved_dir == resolved_forbidden or resolved_dir.is_relative_to(resolved_forbidden):
        raise RuntimeError(f"Refusing protected PostgreSQL data directory '{resolved_dir}'.")


def _allocate_free_loopback_port() -> int:
    """Bind an ephemeral loopback socket on 127.0.0.1 and return an unused non-5432 port."""
    for _ in range(15):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port > 0 and port != 5432:
            return port
    raise RuntimeError("Unable to allocate an ephemeral non-5432 loopback port on 127.0.0.1.")


def _assert_port_free(port: int, *, label: str) -> None:
    """Fail fast if a TCP port is occupied on 127.0.0.1 or ::1; never kill existing processes."""
    if port == 5432:
        raise RuntimeError(f"Refusing protected PostgreSQL port {port} for {label}.")

    # 1. Active listener check on 127.0.0.1
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(
                f"Required {label} port {port} is already occupied on 127.0.0.1. "
                "Stop the conflicting service manually and retry."
            )

    # 2. Exclusive bind check on 127.0.0.1
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as binder:
        exclusive_opt = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if os.name == "nt" and exclusive_opt is not None:
            binder.setsockopt(socket.SOL_SOCKET, exclusive_opt, 1)
        try:
            binder.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(
                f"Required {label} port {port} is unavailable on 127.0.0.1 ({exc}). "
                "Stop the conflicting service manually and retry."
            ) from exc

    # 3. IPv6 ::1 check when IPv6 loopback is enabled
    if socket.has_ipv6:
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe6:
                probe6.settimeout(0.3)
                if probe6.connect_ex(("::1", port)) == 0:
                    raise RuntimeError(
                        f"Required {label} port {port} is already occupied on [::1]. "
                        "Stop the conflicting service manually and retry."
                    )
        except OSError:
            pass

        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as binder6:
                exclusive_opt = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
                if os.name == "nt" and exclusive_opt is not None:
                    binder6.setsockopt(socket.SOL_SOCKET, exclusive_opt, 1)
                binder6.bind(("::1", port))
        except OSError as exc:
            in_use_codes = {errno.EADDRINUSE, 10048, 10013}
            if exc.errno in in_use_codes or getattr(exc, "winerror", None) in in_use_codes:
                raise RuntimeError(
                    f"Required {label} port {port} is already occupied on [::1] ({exc}). "
                    "Stop the conflicting service manually and retry."
                ) from exc


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


def _resolve_runtime_paths(
    *,
    node_exe_raw: str,
    npx_cmd_raw: str,
) -> dict[str, Path]:
    backend_python = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
    backend_alembic = BACKEND_DIR / ".venv" / "Scripts" / "alembic.exe"
    node_exe = Path(node_exe_raw).expanduser().resolve()
    npx_cmd = Path(npx_cmd_raw).expanduser().resolve()
    next_bin = FRONTEND_DIR / "node_modules" / "next" / "dist" / "bin" / "next"
    next_build_dir = FRONTEND_DIR / ".next"
    next_build_id = next_build_dir / "BUILD_ID"

    required_files = [
        ("backend venv Python", backend_python),
        ("backend venv Alembic", backend_alembic),
        ("Node.js executable", node_exe),
        ("npx command", npx_cmd),
        ("Next.js CLI script", next_bin),
        ("Next.js production build marker (.next/BUILD_ID)", next_build_id),
    ]
    if not next_build_dir.is_dir():
        raise FileNotFoundError(
            f"Prebuilt Next.js directory missing: '{next_build_dir}'. Run 'next build' first."
        )
    for label, path in required_files:
        if not path.is_file():
            raise FileNotFoundError(f"Required {label} missing: '{path}'.")

    return {
        "backend_python": backend_python,
        "backend_alembic": backend_alembic,
        "node_exe": node_exe,
        "npx_cmd": npx_cmd,
        "next_bin": next_bin,
    }


def _terminate_process_tree(proc: subprocess.Popen[bytes] | subprocess.Popen[str]) -> None:
    """Terminate an active child process tree cleanly, escalating to force kill if needed."""
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
    """Force-terminate ONLY a verified temporary process PID created by this run."""
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
    """Run a non-daemon tool redirecting stdout/stderr strictly to a log file (no PIPE)."""
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
        else Path(tempfile.gettempdir()).resolve() / f"ecom_e2e_diag_{time_tag}"
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

    _log(f"Preserved redacted diagnostic logs in: {target_dir}", error=True)
    for dest in preserved:
        if dest.name in (
            "initdb.log",
            "pg_ctl_start.log",
            "pg_ctl_stop.log",
            "postgres.log",
            "alembic.log",
            "seed.log",
            "uvicorn.log",
            "nextjs.log",
        ):
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
            raise TimeoutError("Overall watchdog fired during PostgreSQL readiness check.")
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


def _poll_http_health(
    *,
    url: str,
    procs_to_watch: Sequence[tuple[str, subprocess.Popen[bytes]]],
    watchdog_fired: threading.Event,
    deadline_seconds: float = 45.0,
    expect_json_health: bool = False,
) -> None:
    """Poll a loopback HTTP endpoint with bounded timeout and fail-fast process checks."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + deadline_seconds
    last_error = "endpoint did not respond"

    while True:
        if watchdog_fired.is_set():
            raise TimeoutError(f"Overall watchdog fired while waiting for {url}.")
        for proc_name, proc in procs_to_watch:
            rc = proc.poll()
            if rc is not None:
                raise RuntimeError(
                    f"{proc_name} exited prematurely with status {rc} before {url} became ready."
                )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Timed out after {deadline_seconds:.0f}s waiting for {url} ({last_error})."
            )

        req_timeout = min(2.0, max(0.3, remaining))
        try:
            req = urllib.request.Request(url, method="GET")
            with opener.open(req, timeout=req_timeout) as resp:
                status_code = int(resp.status)
                body = resp.read()
                if status_code == 200:
                    if not expect_json_health:
                        return
                    payload = json.loads(body.decode("utf-8", errors="replace"))
                    if (
                        isinstance(payload, dict)
                        and payload.get("status") == "ok"
                        and payload.get("database") == "ok"
                        and payload.get("sandbox_payments") is True
                    ):
                        return
                    last_error = f"unexpected health JSON: {payload!r}"
                else:
                    last_error = f"HTTP {status_code}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)

        sleep_for = min(0.3, max(0.0, deadline - time.monotonic()))
        if sleep_for > 0:
            time.sleep(sleep_for)


def _close_quietly(fh: IO[bytes] | None) -> None:
    if fh is not None:
        try:
            fh.close()
        except OSError:
            pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run live full-stack Playwright E2E suite against disposable PostgreSQL.",
    )
    parser.add_argument(
        "--pg-bin",
        default=os.environ.get("PG_BIN_DIR") or DEFAULT_PG_BIN,
        help="Path to PostgreSQL bin directory (default: PG_BIN_DIR or E:\\databasesql\\bin).",
    )
    parser.add_argument(
        "--node-exe",
        default=str(DEFAULT_NODE_EXE),
        help="Path to Node.js executable (default: C:\\Program Files\\nodejs\\node.exe).",
    )
    parser.add_argument(
        "--npx-cmd",
        default=str(DEFAULT_NPX_CMD),
        help="Path to npx command (default: C:\\Program Files\\nodejs\\npx.cmd).",
    )
    parser.add_argument(
        "--playwright-timeout",
        type=float,
        default=240.0,
        help="Timeout in seconds for Playwright execution (default: 240).",
    )
    parser.add_argument(
        "--overall-timeout",
        type=float,
        default=360.0,
        help="Overall execution watchdog timeout in seconds (default: 360).",
    )
    parser.add_argument(
        "--diag-dir",
        default=None,
        help="Directory to preserve redacted logs on failure (default: %%TEMP%%\\ecom_e2e_diag_*).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Orchestrate disposable PG, backend, frontend, Playwright E2E, and verified cleanup."""
    parser = _build_parser()
    args, extra_args = parser.parse_known_args(list(argv) if argv is not None else None)
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]

    if args.playwright_timeout <= 0 or args.overall_timeout <= 0:
        _log("ERROR: --playwright-timeout and --overall-timeout must be positive.", error=True)
        return 2

    try:
        binaries = _resolve_pg_binaries(args.pg_bin)
        runtime_paths = _resolve_runtime_paths(
            node_exe_raw=args.node_exe,
            npx_cmd_raw=args.npx_cmd,
        )
        _assert_port_free(BACKEND_PORT, label="backend uvicorn")
        _assert_port_free(FRONTEND_PORT, label="frontend next")
        pg_port = _allocate_free_loopback_port()
    except (OSError, RuntimeError, FileNotFoundError) as exc:
        _log(f"ERROR: {exc}", error=True)
        return 1

    run_dir = Path(tempfile.mkdtemp(prefix="ecom_e2e_pg18_")).resolve()
    _validate_temp_run_dir(run_dir)
    data_dir = run_dir / "data"
    initdb_log = run_dir / "initdb.log"
    pg_ctl_start_log = run_dir / "pg_ctl_start.log"
    pg_ctl_stop_log = run_dir / "pg_ctl_stop.log"
    postgres_log = run_dir / "postgres.log"
    alembic_log = run_dir / "alembic.log"
    seed_log = run_dir / "seed.log"
    uvicorn_log = run_dir / "uvicorn.log"
    nextjs_log = run_dir / "nextjs.log"

    base_env = _sanitized_base_env()
    role_password = secrets.token_urlsafe(24)
    database_url = (
        f"postgresql+psycopg://{TEST_ROLE_NAME}:{quote_plus(role_password)}"
        f"@127.0.0.1:{pg_port}/{TEST_DB_NAME}"
    )

    csrf_secret = secrets.token_urlsafe(32)
    guest_order_secret = secrets.token_urlsafe(32)
    webhook_secret = secrets.token_urlsafe(32)

    demo_admin_pw = os.environ.get("DEMO_ADMIN_PASSWORD") or f"E2EAdmin!{secrets.token_hex(6)}"
    demo_staff_pw = os.environ.get("DEMO_STAFF_PASSWORD") or f"E2EStaff!{secrets.token_hex(6)}"
    demo_customer_pw = (
        os.environ.get("DEMO_CUSTOMER_PASSWORD") or f"E2ECust!{secrets.token_hex(6)}"
    )

    secrets_to_scrub = (
        role_password,
        quote_plus(role_password),
        database_url,
        csrf_secret,
        guest_order_secret,
        webhook_secret,
        demo_admin_pw,
        demo_staff_pw,
        demo_customer_pw,
    )

    pg_env = dict(base_env)

    backend_env = dict(base_env)
    backend_env.update(
        {
            "DATABASE_URL": database_url,
            "ENVIRONMENT": "test",
            "COOKIE_SECURE": "false",
            "ALLOWED_ORIGINS": E2E_BASE_URL,
            "PAYMENT_SANDBOX_MODE": "true",
            "CSRF_HMAC_SECRET": csrf_secret,
            "GUEST_ORDER_HMAC_SECRET": guest_order_secret,
            "PAYMENT_WEBHOOK_SECRET": webhook_secret,
            "DEMO_ADMIN_PASSWORD": demo_admin_pw,
            "DEMO_STAFF_PASSWORD": demo_staff_pw,
            "DEMO_CUSTOMER_PASSWORD": demo_customer_pw,
            "PYTHONUNBUFFERED": "1",
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )

    frontend_env = dict(base_env)
    frontend_env.update(
        {
            "NODE_ENV": "production",
            "NEXT_TELEMETRY_DISABLED": "1",
            "NEXT_PUBLIC_API_BASE_URL": PUBLIC_API_BASE_URL,
            "INTERNAL_API_BASE_URL": INTERNAL_API_BASE_URL,
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )

    playwright_env = dict(base_env)
    playwright_env.update(
        {
            "E2E_BASE_URL": E2E_BASE_URL,
            "E2E_ADMIN_EMAIL": DEMO_ADMIN_EMAIL,
            "E2E_ADMIN_PASSWORD": demo_admin_pw,
            "E2E_STAFF_EMAIL": DEMO_STAFF_EMAIL,
            "E2E_STAFF_PASSWORD": demo_staff_pw,
            "E2E_CUSTOMER_EMAIL": DEMO_CUSTOMER_EMAIL,
            "E2E_CUSTOMER_PASSWORD": demo_customer_pw,
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )

    postmaster_pid: int | None = None
    cluster_started = False
    uvicorn_proc: subprocess.Popen[bytes] | None = None
    uvicorn_pid: int | None = None
    uvicorn_log_fh: IO[bytes] | None = None
    nextjs_proc: subprocess.Popen[bytes] | None = None
    nextjs_pid: int | None = None
    nextjs_log_fh: IO[bytes] | None = None
    exit_code = 1

    proc_lock = threading.Lock()
    active_step_proc: subprocess.Popen[bytes] | None = None
    watchdog_fired = threading.Event()

    def _on_overall_timeout() -> None:
        watchdog_fired.set()
        _log(
            f"WATCHDOG TIMEOUT ({args.overall_timeout:.0f}s) exceeded; terminating active "
            "processes and tearing down E2E stack.",
            error=True,
        )
        with proc_lock:
            if active_step_proc is not None:
                _terminate_process_tree(active_step_proc)
            if nextjs_proc is not None:
                _terminate_process_tree(nextjs_proc)
            if uvicorn_proc is not None:
                _terminate_process_tree(uvicorn_proc)

    def _run_watched_logged(
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        log_path: Path,
        timeout_seconds: float,
        stage_name: str,
    ) -> int:
        nonlocal active_step_proc
        with log_path.open("wb") as log_fh:
            with proc_lock:
                if watchdog_fired.is_set():
                    return 124
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(cwd),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                )
                active_step_proc = proc

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
                    if active_step_proc is proc:
                        active_step_proc = None

    watchdog = threading.Timer(args.overall_timeout, _on_overall_timeout)
    watchdog.daemon = True
    watchdog.start()

    try:
        # 1. initdb
        _log(f"initdb in temporary cluster dir '{run_dir}' (pg_port={pg_port})")
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
            cwd=run_dir,
            env=pg_env,
            log_path=initdb_log,
            timeout_seconds=90.0,
            stage_name="initdb.exe",
        )
        if watchdog_fired.is_set():
            exit_code = 124
            return exit_code
        if init_rc != 0:
            raise RuntimeError(f"initdb.exe exited with status {init_rc}.")

        # 2. Start disposable PostgreSQL cluster
        _log(f"starting disposable PostgreSQL on 127.0.0.1:{pg_port}")
        start_rc = _run_watched_logged(
            [
                str(binaries["pg_ctl"]),
                "-D",
                str(data_dir),
                "-l",
                str(postgres_log),
                "-o",
                f"-p {pg_port} -h 127.0.0.1",
                "start",
                "-w",
                "-t",
                "45",
            ],
            cwd=run_dir,
            env=pg_env,
            log_path=pg_ctl_start_log,
            timeout_seconds=45.0,
            stage_name="pg_ctl.exe start",
        )
        postmaster_pid = _read_postmaster_pid(data_dir)
        if watchdog_fired.is_set():
            exit_code = 124
            return exit_code
        if start_rc != 0:
            raise RuntimeError(f"pg_ctl.exe start exited with status {start_rc}.")
        cluster_started = True

        pg_version = _probe_cluster_readiness(
            pg_isready_exe=binaries["pg_isready"],
            psql_exe=binaries["psql"],
            port=pg_port,
            pg_env=pg_env,
            watchdog_fired=watchdog_fired,
            deadline_seconds=20.0,
        )
        _log(f"PostgreSQL ready (pid={postmaster_pid}): {pg_version}")

        # 3. Provision least-privilege role and database
        _log(f"provisioning {TEST_ROLE_NAME} and {TEST_DB_NAME}")
        _provision_least_privilege_db(
            psql_exe=binaries["psql"],
            port=pg_port,
            role_password=role_password,
            pg_env=pg_env,
        )

        # 4. Run Alembic migrations and seed demo catalog & users
        _log("running alembic upgrade head")
        alembic_rc = _run_watched_logged(
            [str(runtime_paths["backend_alembic"]), "upgrade", "head"],
            cwd=BACKEND_DIR,
            env=backend_env,
            log_path=alembic_log,
            timeout_seconds=60.0,
            stage_name="alembic upgrade head",
        )
        if watchdog_fired.is_set():
            exit_code = 124
            return exit_code
        if alembic_rc != 0:
            raise RuntimeError(f"alembic upgrade head exited with status {alembic_rc}.")

        _log("seeding demo catalog and demo users")
        seed_rc = _run_watched_logged(
            [
                str(runtime_paths["backend_python"]),
                "-m",
                "app.cli",
                "seed-demo",
                "--with-demo-users",
            ],
            cwd=BACKEND_DIR,
            env=backend_env,
            log_path=seed_log,
            timeout_seconds=60.0,
            stage_name="seed-demo",
        )
        if watchdog_fired.is_set():
            exit_code = 124
            return exit_code
        if seed_rc != 0:
            raise RuntimeError(f"seed-demo exited with status {seed_rc}.")

        # 5. Start live FastAPI backend (uvicorn)
        _log(f"starting uvicorn on {BACKEND_HOST}:{BACKEND_PORT}")
        uvicorn_log_fh = uvicorn_log.open("wb")
        with proc_lock:
            if watchdog_fired.is_set():
                exit_code = 124
                return exit_code
            uvicorn_proc = subprocess.Popen(
                [
                    str(runtime_paths["backend_python"]),
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    BACKEND_HOST,
                    "--port",
                    str(BACKEND_PORT),
                ],
                cwd=str(BACKEND_DIR),
                env=backend_env,
                stdin=subprocess.DEVNULL,
                stdout=uvicorn_log_fh,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
            uvicorn_pid = uvicorn_proc.pid

        _poll_http_health(
            url=f"http://{BACKEND_HOST}:{BACKEND_PORT}/api/v1/health",
            procs_to_watch=[("uvicorn", uvicorn_proc)],
            watchdog_fired=watchdog_fired,
            deadline_seconds=45.0,
            expect_json_health=True,
        )
        _log(f"backend healthy (pid={uvicorn_pid}) at {PUBLIC_API_BASE_URL}/health")

        # 6. Start live Next.js production server
        _log(f"starting next start on {FRONTEND_HOST}:{FRONTEND_PORT}")
        nextjs_log_fh = nextjs_log.open("wb")
        with proc_lock:
            if watchdog_fired.is_set():
                exit_code = 124
                return exit_code
            nextjs_proc = subprocess.Popen(
                [
                    str(runtime_paths["node_exe"]),
                    str(runtime_paths["next_bin"]),
                    "start",
                    "-H",
                    FRONTEND_HOST,
                    "-p",
                    str(FRONTEND_PORT),
                ],
                cwd=str(FRONTEND_DIR),
                env=frontend_env,
                stdin=subprocess.DEVNULL,
                stdout=nextjs_log_fh,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
            nextjs_pid = nextjs_proc.pid

        _poll_http_health(
            url=f"http://{FRONTEND_HOST}:{FRONTEND_PORT}/",
            procs_to_watch=[("uvicorn", uvicorn_proc), ("next start", nextjs_proc)],
            watchdog_fired=watchdog_fired,
            deadline_seconds=45.0,
            expect_json_health=False,
        )
        _log(f"frontend healthy (pid={nextjs_pid}) at {E2E_BASE_URL}/")

        # 7. Run Playwright E2E suite
        pw_cmd = [str(runtime_paths["npx_cmd"]), "playwright", "test", *extra_args]
        _log(
            f"running Playwright ({' '.join(extra_args) if extra_args else 'all specs'}) "
            f"[timeout={args.playwright_timeout:.0f}s]"
        )
        with proc_lock:
            if watchdog_fired.is_set():
                exit_code = 124
                return exit_code
            pw_proc = subprocess.Popen(
                pw_cmd,
                cwd=str(FRONTEND_DIR),
                env=playwright_env,
                stdin=subprocess.DEVNULL,
                close_fds=True,
            )
            active_step_proc = pw_proc

        pw_deadline = time.monotonic() + float(args.playwright_timeout)
        while True:
            rc = pw_proc.poll()
            if rc is not None:
                exit_code = int(rc)
                break
            if watchdog_fired.is_set():
                _terminate_process_tree(pw_proc)
                exit_code = 124
                break
            remaining = pw_deadline - time.monotonic()
            if remaining <= 0:
                _log(
                    f"ERROR: Playwright exceeded --playwright-timeout "
                    f"({args.playwright_timeout:.0f}s); terminating.",
                    error=True,
                )
                _terminate_process_tree(pw_proc)
                exit_code = 124
                break
            time.sleep(min(0.25, remaining))

        with proc_lock:
            if active_step_proc is pw_proc:
                active_step_proc = None

        if watchdog_fired.is_set():
            exit_code = 124
        _log(f"Playwright finished with exit code {exit_code}")

    except (OSError, RuntimeError, TimeoutError, ValueError, subprocess.SubprocessError) as exc:
        _log(f"ERROR: {_redact_secrets(str(exc), secrets_to_scrub)}", error=True)
        exit_code = 124 if watchdog_fired.is_set() else 1

    finally:
        watchdog.cancel()

        if nextjs_proc is not None:
            _log(f"stopping Next.js server (pid={nextjs_pid})")
            _terminate_process_tree(nextjs_proc)
        _close_quietly(nextjs_log_fh)

        if uvicorn_proc is not None:
            _log(f"stopping Uvicorn server (pid={uvicorn_pid})")
            _terminate_process_tree(uvicorn_proc)
        _close_quietly(uvicorn_log_fh)

        if postmaster_pid is None and data_dir.is_dir():
            postmaster_pid = _read_postmaster_pid(data_dir)

        if cluster_started or postmaster_pid is not None:
            _log(f"stopping PostgreSQL cluster in '{data_dir}' (pid={postmaster_pid})")
            try:
                stop_rc = _run_logged_tool_simple(
                    [
                        str(binaries["pg_ctl"]),
                        "-D",
                        str(data_dir),
                        "-m",
                        "fast",
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

        # Verify every temporary daemon PID exited completely
        lingering = False
        for label, pid in (
            ("Next.js", nextjs_pid),
            ("Uvicorn", uvicorn_pid),
            ("PostgreSQL", postmaster_pid),
        ):
            if pid is None:
                continue
            if _is_pid_running(pid):
                _log(
                    f"WARNING: temporary {label} PID {pid} still alive after stop; forcing exit.",
                    error=True,
                )
                _force_kill_temp_pid(pid)
                time.sleep(0.25)
            if _is_pid_running(pid):
                _log(
                    f"ERROR: temporary {label} PID {pid} remained alive after forced termination!",
                    error=True,
                )
                lingering = True

        if lingering and exit_code == 0:
            exit_code = 1

        if exit_code != 0 or watchdog_fired.is_set():
            _preserve_and_print_logs(
                source_logs=[
                    postgres_log,
                    pg_ctl_start_log,
                    pg_ctl_stop_log,
                    initdb_log,
                    alembic_log,
                    seed_log,
                    uvicorn_log,
                    nextjs_log,
                ],
                diag_dir=Path(args.diag_dir) if args.diag_dir else None,
                secrets_to_scrub=secrets_to_scrub,
            )

        _log(f"cleaning tempdir '{run_dir}'")
        shutil.rmtree(run_dir, ignore_errors=True)
        if not lingering:
            _log("Clean teardown verified; zero temporary processes remain.")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
