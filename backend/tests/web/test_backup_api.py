from pathlib import Path

import pytest

from app.services import backup

pytestmark = pytest.mark.db


async def test_backup_endpoint_dumps_database_or_explains(api, data_dir):
    r = await api.post("/api/v1/backups")
    if backup.which("pg_dump") is None and not backup.docker_usable():
        assert r.status_code == 503 and r.json()["error"]["code"] == "BACKUP_UNAVAILABLE"
        return
    assert r.status_code == 201, r.text
    body = r.json()
    path = Path(body["path"])
    assert path.parent == data_dir / "backups" and path.read_bytes()[:5] == b"PGDMP"
    items = (await api.get("/api/v1/backups")).json()["items"]
    assert items[0]["file"] == body["file"]
