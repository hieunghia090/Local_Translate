import os
import subprocess
from datetime import datetime

import pytest

from app.config import get_settings
from app.errors import AppError
from app.services import backup

MISMATCH = b"pg_dump: error: aborting because of server version mismatch"


@pytest.fixture
def fake(data_dir, monkeypatch):
    """`tools`: công cụ có trên máy. `results`: mã thoát lần lượt của từng lần chạy."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://lt:pw@127.0.0.1:5433/local_translate")
    monkeypatch.setenv("POSTGRES_PORT", "5433")
    get_settings.cache_clear()
    state = {"tools": {"pg_dump": "/usr/bin/pg_dump", "docker": "/usr/bin/docker"}, "results": [], "calls": []}

    def fake_run(cmd, *, env, stdout_path):
        state["calls"].append((cmd, env))
        code = state["results"].pop(0)
        if code == 0:
            stdout_path.write_bytes(b"PGDMP fake")
        return subprocess.CompletedProcess(cmd, code, stderr=b"" if code == 0 else MISMATCH)

    monkeypatch.setattr(backup, "which", lambda name: state["tools"].get(name))
    monkeypatch.setattr(backup, "run", fake_run)
    yield state
    get_settings.cache_clear()


def test_local_pg_dump_with_password_in_env(fake, data_dir):
    fake["results"] = [0]
    r = backup.create_backup(now=datetime(2026, 10, 4, 9, 5, 7))
    assert r["file"] == "local_translate_20261004-090507.dump" and r["method"] == "local"
    assert r["path"] == str(data_dir / "backups" / r["file"]) and r["size_bytes"] == len(b"PGDMP fake")
    cmd, env = fake["calls"][0]
    assert cmd[0] == "/usr/bin/pg_dump" and "--format=custom" in cmd
    assert cmd[cmd.index("--port") + 1] == "5433" and cmd[cmd.index("--dbname") + 1] == "local_translate"
    assert env["PGPASSWORD"] == "pw" and "pw" not in " ".join(cmd)


def test_version_mismatch_falls_back_to_docker_compose(fake, data_dir):
    # Review Focus 5
    fake["results"] = [1, 0]
    r = backup.create_backup()
    assert r["method"] == "docker"
    cmd, env = fake["calls"][1]
    assert cmd[:3] == ["docker", "compose", "-f"] and cmd[4:8] == ["exec", "-T", "db", "pg_dump"]
    assert cmd[cmd.index("--dbname") + 1] == "local_translate" and env is None
    assert [p.name for p in (data_dir / "backups").iterdir()] == [r["file"]]  # không còn .part


def test_docker_not_used_when_database_is_not_compose(fake, monkeypatch):
    # Review Focus 5: DATABASE_URL trỏ Postgres khác thì không được dump DB của compose
    monkeypatch.setenv("DATABASE_URL", "postgresql://lt:pw@127.0.0.1:6543/other")
    get_settings.cache_clear()
    fake["tools"] = {"docker": "/usr/bin/docker"}
    with pytest.raises(AppError) as e:
        backup.create_backup()
    assert e.value.code == "BACKUP_UNAVAILABLE" and e.value.status == 503 and fake["calls"] == []


def test_no_tool_gives_install_hint(fake):
    fake["tools"] = {}
    with pytest.raises(AppError) as e:
        backup.create_backup()
    assert e.value.code == "BACKUP_UNAVAILABLE" and "pg_dump" in e.value.message and "make db" in e.value.message


def test_all_attempts_fail_reports_stderr_and_cleans_up(fake, data_dir):
    fake["results"] = [1, 1]
    with pytest.raises(AppError) as e:
        backup.create_backup()
    assert e.value.code == "BACKUP_FAILED" and "server version mismatch" in e.value.message
    assert list((data_dir / "backups").iterdir()) == []


def test_list_backups_newest_first(fake, data_dir):
    folder = data_dir / "backups"
    folder.mkdir(parents=True)
    old, new = folder / "a.dump", folder / "b.dump"
    old.write_bytes(b"1")
    new.write_bytes(b"22")
    os.utime(old, (1_000_000, 1_000_000))
    (folder / "c.dump.part").write_bytes(b"x")
    assert [b["file"] for b in backup.list_backups()] == ["b.dump", "a.dump"]


def test_same_second_backups_get_separate_part_files(fake, data_dir, monkeypatch):
    now = datetime(2026, 10, 4, 9, 5, 7)
    inner = {}
    orig = backup.run

    def nested_run(cmd, *, env, stdout_path):
        if not inner:  # backup thứ hai bắt đầu khi backup đầu đang chạy
            inner["part"] = stdout_path
            inner["result"] = backup.create_backup(now=now)
        return orig(cmd, env=env, stdout_path=stdout_path)

    monkeypatch.setattr(backup, "run", nested_run)
    fake["results"] = [0, 0]
    first = backup.create_backup(now=now)
    assert inner["result"]["file"] == "local_translate_20261004-090507-2.dump"
    assert first["file"] == "local_translate_20261004-090507.dump"
    assert sorted(p.name for p in (data_dir / "backups").iterdir()) == sorted([first["file"], inner["result"]["file"]])
