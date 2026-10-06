"""Sao lưu database bằng pg_dump vào DATA_DIR/backups (spec 00 mục 8).

Thứ tự thử: pg_dump trên máy, rồi `docker compose exec db pg_dump`. Docker chỉ được dùng khi DATABASE_URL trỏ đúng
Postgres của docker compose trong repo, để không bao giờ dump nhầm một database khác.
"""
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import ROOT, get_settings
from app.errors import AppError

COMPOSE_FILE = ROOT / "docker-compose.yml"
TIMEOUT_S = 600
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
MSG_UNAVAILABLE = (
    "Không tìm thấy pg_dump trên máy và không dùng được docker compose. "
    "Cài PostgreSQL client 16 (vd. `brew install libpq`) hoặc chạy Postgres bằng `make db`."
)


def which(name: str) -> str | None:
    return shutil.which(name)


def run(cmd: list[str], *, env: dict[str, str] | None, stdout_path: Path) -> subprocess.CompletedProcess:
    with stdout_path.open("wb") as out:
        return subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE, env=env, timeout=TIMEOUT_S, check=False)


def backups_dir() -> Path:
    return get_settings().data_path / "backups"


def _url():
    return make_url(get_settings().database_url)


def docker_usable() -> bool:
    url = _url()
    return (
        which("docker") is not None
        and COMPOSE_FILE.is_file()
        and (url.host or "127.0.0.1") in _LOCAL_HOSTS
        and (url.port or 5432) == get_settings().postgres_port
    )


def local_command(pg_dump: str) -> tuple[list[str], dict[str, str]]:
    url = _url()
    cmd = [pg_dump, "--format=custom", "--no-owner",
           "--host", url.host or "127.0.0.1", "--port", str(url.port or 5432),
           "--username", url.username or "postgres", "--dbname", url.database or "postgres"]
    return cmd, {**os.environ, "PGPASSWORD": url.password or ""}


def docker_command() -> list[str]:
    url = _url()
    return ["docker", "compose", "-f", str(COMPOSE_FILE), "exec", "-T", "db", "pg_dump", "--format=custom", "--no-owner",
            "--username", url.username or "local_translate", "--dbname", url.database or "local_translate"]


def _stderr(proc: subprocess.CompletedProcess) -> str:
    return (proc.stderr or b"").decode("utf-8", "replace").strip()[-500:] or f"mã thoát {proc.returncode}"


def _reserve_part(final: Path) -> tuple[Path, Path]:
    """Giữ chỗ file .part độc quyền; trùng tên (cùng giây) thì thêm -2, -3… trước đuôi."""
    n = 1
    while True:
        cand = final if n == 1 else final.with_name(f"{final.stem}-{n}{final.suffix}")
        part = cand.with_name(cand.name + ".part")
        if not cand.exists():
            try:
                part.open("x").close()
                return cand, part
            except FileExistsError:
                pass
        n += 1


def create_backup(now: datetime | None = None) -> dict:
    attempts: list[tuple[str, list[str], dict[str, str] | None]] = []
    local = which("pg_dump")
    if local:
        cmd, env = local_command(local)
        attempts.append(("local", cmd, env))
    if docker_usable():
        attempts.append(("docker", docker_command(), None))
    if not attempts:
        raise AppError("BACKUP_UNAVAILABLE", MSG_UNAVAILABLE, 503)

    folder = backups_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now().astimezone()).strftime("%Y%m%d-%H%M%S")
    final = folder / f"{_url().database or 'db'}_{stamp}.dump"
    final, part = _reserve_part(final)
    errors: list[str] = []
    for method, cmd, env in attempts:
        try:
            proc = run(cmd, env=env, stdout_path=part)
        except (OSError, subprocess.TimeoutExpired) as e:
            errors.append(f"{method}: {e}")
            continue
        if proc.returncode == 0 and part.stat().st_size > 0:
            part.replace(final)
            return {"file": final.name, "path": str(final), "size_bytes": final.stat().st_size, "method": method}
        errors.append(f"{method}: {_stderr(proc)}")
    part.unlink(missing_ok=True)
    raise AppError("BACKUP_FAILED", "Sao lưu thất bại. " + " | ".join(errors), 500, {"errors": errors})


def list_backups() -> list[dict]:
    folder = backups_dir()
    if not folder.is_dir():
        return []
    files = sorted(folder.glob("*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {"file": p.name, "path": str(p), "size_bytes": p.stat().st_size,
         "created_at": datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat()}
        for p in files
    ]


if __name__ == "__main__":
    try:
        print(create_backup()["path"])
    except AppError as e:
        print(e.message, file=sys.stderr)
        sys.exit(1)
