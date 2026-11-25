"""Windows-safe reproducible Locust load runner (10 / 50 / 100 VUs) against disposable PG 18.6."""

from __future__ import annotations

import argparse
import contextlib
import csv
import ctypes
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
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any
from urllib.parse import quote_plus

LOAD_DIR = Path(__file__).resolve().parent
TESTS_DIR = LOAD_DIR.parent
BACKEND_DIR = TESTS_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.database import create_engine_from_url  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.identity import User  # noqa: E402
from app.models.inventory import InventoryStock, ProductVariant  # noqa: E402

DEFAULT_PG_BIN = r"E:\databasesql\bin"
FORBIDDEN_DATA_DIR = Path(r"E:\databasesql\data")
TEST_ROLE_NAME = "ecom_load_role"
TEST_DB_NAME = "ecom_load_db"
SCARCITY_SKU = "ATL-LINEN-SND-L"
SCARCITY_INITIAL_ON_HAND = 5
ABUNDANT_ON_HAND = 100_000
CUSTOMER_POOL_SIZE = 120
LOAD_CUSTOMER_PASSWORD = "AtelierLoad!2026"
TIERS = (10, 50, 100)

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87


def _log(message: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    print(f"[load-runner] {message}", file=stream, flush=True)


def _sanitized_base_env() -> dict[str, str]:
    strip_keys = {
        "DATABASE_URL",
        "TEST_DATABASE_URL",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "CSRF_HMAC_SECRET",
        "GUEST_ORDER_HMAC_SECRET",
        "PAYMENT_WEBHOOK_SECRET",
        "METRICS_BEARER_TOKEN",
    }
    return {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith("PG") and k.upper() not in strip_keys
    }


def _allocate_free_loopback_port() -> int:
    for _ in range(15):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port > 0 and port not in (5432, 8000, 3000):
            return port
    raise RuntimeError("Unable to allocate an ephemeral non-5432 loopback port.")


def _validate_temp_run_dir(run_dir: Path) -> None:
    resolved_dir = run_dir.resolve()
    resolved_temp = Path(tempfile.gettempdir()).resolve()
    if not resolved_dir.is_relative_to(resolved_temp):
        raise RuntimeError(f"Refusing non-temp run directory '{resolved_dir}'.")
    resolved_forbidden = FORBIDDEN_DATA_DIR.resolve()
    if resolved_dir == resolved_forbidden or resolved_dir.is_relative_to(resolved_forbidden):
        raise RuntimeError(f"Refusing protected PostgreSQL data directory '{resolved_dir}'.")


def _resolve_pg_binaries(pg_bin_raw: str) -> dict[str, Path]:
    pg_bin = Path(pg_bin_raw).expanduser().resolve()
    if not pg_bin.is_dir():
        raise FileNotFoundError(f"PostgreSQL bin directory not found: '{pg_bin}'.")
    suffix = ".exe" if os.name == "nt" else ""
    binaries: dict[str, Path] = {}
    for tool in ("initdb", "pg_ctl", "pg_isready", "psql"):
        candidate = pg_bin / f"{tool}{suffix}"
        if not candidate.is_file():
            raise FileNotFoundError(f"Required PostgreSQL binary missing: '{candidate}'.")
        binaries[tool] = candidate
    return binaries


def _terminate_process_tree(proc: subprocess.Popen[bytes] | subprocess.Popen[str]) -> None:
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
                get_last_error = getattr(ctypes, "get_last_error", None)
                if get_last_error is not None and int(get_last_error()) == _ERROR_INVALID_PARAMETER:
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
            os.kill(pid, int(getattr(signal, "SIGKILL", signal.SIGTERM)))
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


def _run_logged_tool(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
    timeout_seconds: float,
) -> int:
    with log_path.open("wb") as log_fh:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            close_fds=True,
            timeout=timeout_seconds,
            check=False,
        )
    return int(proc.returncode)


def _probe_pg_readiness(
    *,
    pg_isready_exe: Path,
    psql_exe: Path,
    port: int,
    pg_env: dict[str, str],
    deadline_seconds: float = 20.0,
) -> str:
    deadline = time.monotonic() + deadline_seconds
    last_error = "not ready"
    while time.monotonic() < deadline:
        ready = subprocess.run(
            [str(pg_isready_exe), "-h", "127.0.0.1", "-p", str(port), "-U", "postgres"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env=pg_env,
            timeout=3,
            check=False,
        )
        if ready.returncode == 0:
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
                timeout=5,
                check=False,
            )
            if sql_res.returncode == 0 and sql_res.stdout.strip():
                return sql_res.stdout.strip().splitlines()[0].strip()
            last_error = sql_res.stderr.strip() or f"psql exit {sql_res.returncode}"
        else:
            last_error = f"pg_isready exit {ready.returncode}"
        time.sleep(0.25)
    raise TimeoutError(f"PostgreSQL on 127.0.0.1:{port} did not become ready ({last_error}).")


def _provision_db(
    *,
    psql_exe: Path,
    port: int,
    role_password: str,
    pg_env: dict[str, str],
) -> None:
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
        raise RuntimeError(f"psql provisioning failed: {res.stderr.strip()}")


def _poll_uvicorn_health(
    *,
    url: str,
    proc: subprocess.Popen[bytes],
    deadline_seconds: float = 30.0,
) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + deadline_seconds
    last_error = "no response"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"uvicorn exited prematurely with code {proc.returncode}")
        try:
            with opener.open(urllib.request.Request(url, method="GET"), timeout=2.0) as resp:
                if int(resp.status) == 200:
                    payload = json.loads(resp.read().decode("utf-8"))
                    if payload.get("status") == "ok" and payload.get("database") == "ok":
                        return
                    last_error = f"unexpected payload {payload!r}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise TimeoutError(f"uvicorn health probe failed ({last_error})")


def _prepare_load_stock_and_users(
    *,
    database_url: str,
    csrf_secret: str,
    guest_secret: str,
    webhook_secret: str,
) -> dict[str, Any]:
    settings = Settings(
        database_url=database_url,
        environment="test",
        cookie_secure=False,
        allowed_origins=["http://127.0.0.1:8000"],
        csrf_hmac_secret=csrf_secret,
        guest_order_hmac_secret=guest_secret,
        payment_webhook_secret=webhook_secret,
        payment_sandbox_mode=True,
    )
    engine = create_engine_from_url(database_url, settings)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        with factory() as db:
            # Boost non-scarcity variants, keep ATL-LINEN-SND-L tight at 5 units
            rows = db.execute(
                select(ProductVariant, InventoryStock).join(
                    InventoryStock, InventoryStock.variant_id == ProductVariant.id
                )
            ).all()
            boosted_skus: list[str] = []
            for variant, stock in rows:
                if variant.sku == SCARCITY_SKU:
                    stock.on_hand = SCARCITY_INITIAL_ON_HAND
                    stock.reserved = 0
                else:
                    stock.on_hand = ABUNDANT_ON_HAND
                    stock.reserved = 0
                    boosted_skus.append(variant.sku)

            pw_hash = hash_password(LOAD_CUSTOMER_PASSWORD)
            for idx in range(1, CUSTOMER_POOL_SIZE + 1):
                email = f"load.vu.{idx}@example.com"
                if db.scalar(select(User.id).where(User.email == email)) is None:
                    db.add(
                        User(
                            email=email,
                            password_hash=pw_hash,
                            full_name=f"Load VU {idx}",
                            role="customer",
                            is_active=True,
                        )
                    )
            db.commit()
            return {
                "scarcity_sku": SCARCITY_SKU,
                "scarcity_initial_on_hand": SCARCITY_INITIAL_ON_HAND,
                "boosted_skus_count": len(boosted_skus),
                "load_customers_seeded": CUSTOMER_POOL_SIZE,
            }
    finally:
        engine.dispose()


def _verify_pg_invariants(
    *,
    database_url: str,
    csrf_secret: str,
    guest_secret: str,
    webhook_secret: str,
) -> dict[str, Any]:
    settings = Settings(
        database_url=database_url,
        environment="test",
        cookie_secure=False,
        allowed_origins=["http://127.0.0.1:8000"],
        csrf_hmac_secret=csrf_secret,
        guest_order_hmac_secret=guest_secret,
        payment_webhook_secret=webhook_secret,
        payment_sandbox_mode=True,
    )
    engine = create_engine_from_url(database_url, settings)
    try:
        with Session(engine) as db:
            neg_reserved = int(
                db.scalar(text("SELECT COUNT(*) FROM inventory_stocks WHERE reserved < 0")) or 0
            )
            neg_on_hand = int(
                db.scalar(text("SELECT COUNT(*) FROM inventory_stocks WHERE on_hand < 0")) or 0
            )
            reserved_gt_on_hand = int(
                db.scalar(text("SELECT COUNT(*) FROM inventory_stocks WHERE reserved > on_hand"))
                or 0
            )
            reservation_mismatch = int(
                db.scalar(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM inventory_stocks s
                        LEFT JOIN (
                            SELECT variant_id, COALESCE(SUM(quantity), 0) AS active_sum
                            FROM inventory_reservations
                            WHERE status = 'ACTIVE'
                            GROUP BY variant_id
                        ) r ON r.variant_id = s.variant_id
                        WHERE s.reserved <> COALESCE(r.active_sum, 0)
                        """
                    )
                )
                or 0
            )
            multi_succeeded = int(
                db.scalar(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM (
                            SELECT order_id
                            FROM payment_intents
                            WHERE status = 'SUCCEEDED'
                            GROUP BY order_id
                            HAVING COUNT(*) > 1
                        ) dup
                        """
                    )
                )
                or 0
            )
            scarcity_row = db.execute(
                text(
                    """
                    SELECT s.on_hand, s.reserved,
                           COALESCE(
                               SUM(CASE WHEN ir.status = 'COMMITTED' THEN ir.quantity ELSE 0 END),
                               0
                           ) AS committed_qty,
                           COALESCE(
                               SUM(CASE WHEN ir.status = 'ACTIVE' THEN ir.quantity ELSE 0 END),
                               0
                           ) AS active_qty
                    FROM product_variants pv
                    JOIN inventory_stocks s ON s.variant_id = pv.id
                    LEFT JOIN inventory_reservations ir ON ir.variant_id = pv.id
                    WHERE pv.sku = :sku
                    GROUP BY s.on_hand, s.reserved
                    """
                ),
                {"sku": SCARCITY_SKU},
            ).first()
            total_orders = int(db.scalar(text("SELECT COUNT(*) FROM orders")) or 0)
            paid_orders = int(
                db.scalar(text("SELECT COUNT(*) FROM orders WHERE payment_status = 'PAID'")) or 0
            )
    finally:
        engine.dispose()

    scarcity_info = {
        "on_hand": int(scarcity_row[0]) if scarcity_row else -1,
        "reserved": int(scarcity_row[1]) if scarcity_row else -1,
        "committed_qty": int(scarcity_row[2]) if scarcity_row else -1,
        "active_qty": int(scarcity_row[3]) if scarcity_row else -1,
    }
    invariants_ok = (
        neg_reserved == 0
        and neg_on_hand == 0
        and reserved_gt_on_hand == 0
        and reservation_mismatch == 0
        and multi_succeeded == 0
        and scarcity_info["on_hand"] >= 0
        and (scarcity_info["committed_qty"] + scarcity_info["active_qty"])
        <= SCARCITY_INITIAL_ON_HAND
    )
    return {
        "invariants_ok": invariants_ok,
        "negative_reserved_count": neg_reserved,
        "negative_on_hand_count": neg_on_hand,
        "reserved_gt_on_hand_count": reserved_gt_on_hand,
        "reservation_mismatch_count": reservation_mismatch,
        "multi_succeeded_orders_count": multi_succeeded,
        "total_orders": total_orders,
        "paid_orders": paid_orders,
        "scarcity_sku": SCARCITY_SKU,
        "scarcity_stats": scarcity_info,
    }


def _scrape_metrics(api_base: str, bearer_token: str) -> dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        f"{api_base}/api/v1/metrics",
        method="GET",
        headers={"Authorization": f"Bearer {bearer_token}"},
    )
    with opener.open(req, timeout=10.0) as resp:
        text_body = resp.read().decode("utf-8", errors="replace")

    interesting_lines = [
        line
        for line in text_body.splitlines()
        if line
        and not line.startswith("#")
        and any(
            line.startswith(prefix)
            for prefix in (
                "db_pool_connections",
                "checkout_events_total",
                "payment_webhook_events_total",
                "http_requests_total",
                "rate_limit_decisions_total",
            )
        )
    ]
    return {
        "status": 200,
        "total_lines": len(text_body.splitlines()),
        "excerpt": interesting_lines,
    }


def _parse_locust_stats_csv(stats_csv: Path) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    with stats_csv.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(dict(row))
    agg = next((r for r in rows if r.get("Name") == "Aggregated"), rows[-1] if rows else {})
    return {
        "aggregated": {
            "request_count": int(agg.get("Request Count", 0) or 0),
            "failure_count": int(agg.get("Failure Count", 0) or 0),
            "median_ms": float(agg.get("Median Response Time", 0) or 0),
            "avg_ms": round(float(agg.get("Average Response Time", 0) or 0), 2),
            "p95_ms": float(agg.get("95%", 0) or 0),
            "p99_ms": float(agg.get("99%", 0) or 0),
            "max_ms": float(agg.get("Max Response Time", 0) or 0),
            "rps": round(float(agg.get("Requests/s", 0) or 0), 2),
        },
        "endpoints": [
            {
                "type": r.get("Type", ""),
                "name": r.get("Name", ""),
                "requests": int(r.get("Request Count", 0) or 0),
                "failures": int(r.get("Failure Count", 0) or 0),
                "median_ms": float(r.get("Median Response Time", 0) or 0),
                "p95_ms": float(r.get("95%", 0) or 0),
                "p99_ms": float(r.get("99%", 0) or 0),
                "rps": round(float(r.get("Requests/s", 0) or 0), 2),
            }
            for r in rows
            if r.get("Name") != "Aggregated"
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run reproducible 10/50/100 VU Locust load suite.")
    parser.add_argument("--pg-bin", default=os.environ.get("PG_BIN_DIR") or DEFAULT_PG_BIN)
    parser.add_argument("--duration", default="15s", help="Per-tier Locust run time (default: 15s)")
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path to write full JSON load summary.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    backend_python = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
    backend_alembic = BACKEND_DIR / ".venv" / "Scripts" / "alembic.exe"
    locust_exe = BACKEND_DIR / ".venv" / "Scripts" / "locust.exe"
    locustfile_path = LOAD_DIR / "locustfile.py"
    for label, p in (
        ("python", backend_python),
        ("alembic", backend_alembic),
        ("locust", locust_exe),
        ("locustfile", locustfile_path),
    ):
        if not p.is_file():
            _log(f"ERROR: Missing {label}: {p}", error=True)
            return 1

    binaries = _resolve_pg_binaries(args.pg_bin)
    pg_port = _allocate_free_loopback_port()
    api_port = _allocate_free_loopback_port()
    while api_port == pg_port:
        api_port = _allocate_free_loopback_port()

    run_dir = Path(tempfile.mkdtemp(prefix="ecom_load_pg18_")).resolve()
    _validate_temp_run_dir(run_dir)
    data_dir = run_dir / "data"
    initdb_log = run_dir / "initdb.log"
    pg_ctl_start_log = run_dir / "pg_ctl_start.log"
    pg_ctl_stop_log = run_dir / "pg_ctl_stop.log"
    postgres_log = run_dir / "postgres.log"
    alembic_log = run_dir / "alembic.log"
    seed_log = run_dir / "seed.log"
    uvicorn_log = run_dir / "uvicorn.log"

    base_env = _sanitized_base_env()
    role_password = secrets.token_urlsafe(24)
    database_url = (
        f"postgresql+psycopg://{TEST_ROLE_NAME}:{quote_plus(role_password)}"
        f"@127.0.0.1:{pg_port}/{TEST_DB_NAME}"
    )
    csrf_secret = secrets.token_urlsafe(32)
    guest_secret = secrets.token_urlsafe(32)
    webhook_secret = secrets.token_urlsafe(32)
    metrics_token = secrets.token_urlsafe(32)
    admin_password = f"LoadAdmin!{secrets.token_hex(6)}"
    staff_password = f"LoadStaff!{secrets.token_hex(6)}"
    customer_password = f"LoadCust!{secrets.token_hex(6)}"
    api_origin = f"http://127.0.0.1:{api_port}"

    backend_env = dict(base_env)
    backend_env.update(
        {
            "DATABASE_URL": database_url,
            "ENVIRONMENT": "test",
            "COOKIE_SECURE": "false",
            "ALLOWED_ORIGINS": api_origin,
            "TRUSTED_PROXY_IPS": "127.0.0.1/32",
            "PAYMENT_SANDBOX_MODE": "true",
            "CSRF_HMAC_SECRET": csrf_secret,
            "GUEST_ORDER_HMAC_SECRET": guest_secret,
            "PAYMENT_WEBHOOK_SECRET": webhook_secret,
            "METRICS_ENABLED": "true",
            "METRICS_BEARER_TOKEN": metrics_token,
            "DB_POOL_SIZE": "15",
            "DB_MAX_OVERFLOW": "25",
            "RESERVATION_SWEEPER_INTERVAL_SECONDS": "0",
            "DEMO_ADMIN_PASSWORD": admin_password,
            "DEMO_STAFF_PASSWORD": staff_password,
            "DEMO_CUSTOMER_PASSWORD": customer_password,
            "PYTHONUNBUFFERED": "1",
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )

    postmaster_pid: int | None = None
    cluster_started = False
    uvicorn_proc: subprocess.Popen[bytes] | None = None
    uvicorn_pid: int | None = None
    uvicorn_log_fh: IO[bytes] | None = None
    exit_code = 1

    try:
        _log(f"initdb in '{run_dir}' (pg_port={pg_port}, api_port={api_port})")
        if (
            _run_logged_tool(
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
                env=base_env,
                log_path=initdb_log,
                timeout_seconds=60.0,
            )
            != 0
        ):
            raise RuntimeError("initdb failed")

        if (
            _run_logged_tool(
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
                    "30",
                ],
                cwd=run_dir,
                env=base_env,
                log_path=pg_ctl_start_log,
                timeout_seconds=35.0,
            )
            != 0
        ):
            raise RuntimeError("pg_ctl start failed")
        cluster_started = True
        postmaster_pid = _read_postmaster_pid(data_dir)

        pg_version = _probe_pg_readiness(
            pg_isready_exe=binaries["pg_isready"],
            psql_exe=binaries["psql"],
            port=pg_port,
            pg_env=base_env,
        )
        _log(f"PostgreSQL ready (pid={postmaster_pid}): {pg_version}")

        _provision_db(
            psql_exe=binaries["psql"],
            port=pg_port,
            role_password=role_password,
            pg_env=base_env,
        )

        if (
            _run_logged_tool(
                [str(backend_alembic), "upgrade", "head"],
                cwd=BACKEND_DIR,
                env=backend_env,
                log_path=alembic_log,
                timeout_seconds=45.0,
            )
            != 0
        ):
            alembic_err = alembic_log.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(f"alembic upgrade head failed: {alembic_err}")

        if (
            _run_logged_tool(
                [str(backend_python), "-m", "app.cli", "seed-demo", "--with-demo-users"],
                cwd=BACKEND_DIR,
                env=backend_env,
                log_path=seed_log,
                timeout_seconds=45.0,
            )
            != 0
        ):
            raise RuntimeError(
                f"seed-demo failed: {seed_log.read_text(encoding='utf-8', errors='replace')}"
            )

        seed_summary = _prepare_load_stock_and_users(
            database_url=database_url,
            csrf_secret=csrf_secret,
            guest_secret=guest_secret,
            webhook_secret=webhook_secret,
        )
        _log(f"Load stock & pool seeded: {seed_summary}")

        uvicorn_log_fh = uvicorn_log.open("wb")
        uvicorn_proc = subprocess.Popen(
            [
                str(backend_python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
            cwd=str(BACKEND_DIR),
            env=backend_env,
            stdin=subprocess.DEVNULL,
            stdout=uvicorn_log_fh,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
        uvicorn_pid = uvicorn_proc.pid
        _poll_uvicorn_health(
            url=f"{api_origin}/api/v1/health",
            proc=uvicorn_proc,
        )
        _log(f"Uvicorn ready (pid={uvicorn_pid}) at {api_origin}")

        tier_reports: list[dict[str, Any]] = []
        for vu_count in TIERS:
            csv_prefix = run_dir / f"tier_{vu_count}"
            status_json_path = run_dir / f"tier_{vu_count}_status.json"
            locust_log = run_dir / f"tier_{vu_count}_locust.log"

            locust_env = dict(base_env)
            locust_env.update(
                {
                    "LOAD_SCARCITY_SKU": SCARCITY_SKU,
                    "LOAD_ADMIN_EMAIL": "admin@atelier.example",
                    "LOAD_ADMIN_PASSWORD": admin_password,
                    "LOAD_CUSTOMER_PASSWORD": LOAD_CUSTOMER_PASSWORD,
                    "LOAD_CUSTOMER_POOL_SIZE": str(CUSTOMER_POOL_SIZE),
                    "LOAD_ALLOWED_ORIGIN": api_origin,
                    "LOCUST_STATUS_SUMMARY_PATH": str(status_json_path),
                    "NO_PROXY": "127.0.0.1,localhost",
                }
            )
            _log(f"Running Locust tier: {vu_count} VUs for {args.duration}")
            rc = _run_logged_tool(
                [
                    str(locust_exe),
                    "-f",
                    str(locustfile_path),
                    "--headless",
                    "--host",
                    api_origin,
                    "-u",
                    str(vu_count),
                    "-r",
                    str(vu_count),
                    "-t",
                    args.duration,
                    "--csv",
                    str(csv_prefix),
                    "--exit-code-on-error",
                    "0",
                ],
                cwd=BACKEND_DIR,
                env=locust_env,
                log_path=locust_log,
                timeout_seconds=90.0,
            )
            if rc not in (0, 1):
                raise RuntimeError(f"Locust tier {vu_count} exited unexpectedly with {rc}")

            parsed_stats = _parse_locust_stats_csv(Path(f"{csv_prefix}_stats.csv"))
            status_breakdown = (
                json.loads(status_json_path.read_text(encoding="utf-8"))
                if status_json_path.is_file()
                else {}
            )
            tier_reports.append(
                {
                    "vus": vu_count,
                    "duration": args.duration,
                    "stats": parsed_stats,
                    "status_breakdown": status_breakdown,
                }
            )
            _log(
                f"Tier {vu_count} VUs complete: reqs={parsed_stats['aggregated']['request_count']} "
                f"failures={parsed_stats['aggregated']['failure_count']} "
                f"rps={parsed_stats['aggregated']['rps']} "
                f"p95={parsed_stats['aggregated']['p95_ms']}ms "
                f"p99={parsed_stats['aggregated']['p99_ms']}ms "
                f"breakdown={status_breakdown}"
            )

        invariants = _verify_pg_invariants(
            database_url=database_url,
            csrf_secret=csrf_secret,
            guest_secret=guest_secret,
            webhook_secret=webhook_secret,
        )
        metrics_data = _scrape_metrics(api_origin, metrics_token)

        final_report = {
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "pg_version": pg_version,
            "db_pool_config": {"pool_size": 15, "max_overflow": 25},
            "seed_summary": seed_summary,
            "tiers": tier_reports,
            "invariants": invariants,
            "metrics": metrics_data,
        }
        if args.output_json:
            Path(args.output_json).write_text(json.dumps(final_report, indent=2), encoding="utf-8")
        print("=== LOAD SUITE JSON REPORT ===", flush=True)
        print(json.dumps(final_report, indent=2), flush=True)

        any_5xx = any(int(t["status_breakdown"].get("5xx", 0)) > 0 for t in tier_reports)
        exit_code = 0 if (invariants["invariants_ok"] and not any_5xx) else 1
    finally:
        if uvicorn_proc is not None:
            _log(f"Stopping uvicorn (pid={uvicorn_pid})")
            _terminate_process_tree(uvicorn_proc)
        if uvicorn_log_fh is not None:
            with contextlib.suppress(OSError):
                uvicorn_log_fh.close()
        if postmaster_pid is None and data_dir.is_dir():
            postmaster_pid = _read_postmaster_pid(data_dir)
        if cluster_started or postmaster_pid is not None:
            _log(f"Stopping PostgreSQL cluster (pid={postmaster_pid})")
            _run_logged_tool(
                [str(binaries["pg_ctl"]), "-D", str(data_dir), "-m", "immediate", "-w", "stop"],
                cwd=run_dir,
                env=base_env,
                log_path=pg_ctl_stop_log,
                timeout_seconds=20.0,
            )
        for label, pid in (("Uvicorn", uvicorn_pid), ("PostgreSQL", postmaster_pid)):
            if pid is not None and _is_pid_running(pid):
                _log(f"Forcing exit of {label} PID {pid}", error=True)
                _force_kill_temp_pid(pid)
                time.sleep(0.25)
                if _is_pid_running(pid):
                    exit_code = 1
        shutil.rmtree(run_dir, ignore_errors=True)
        _log("Clean teardown verified; zero temporary processes remain.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
