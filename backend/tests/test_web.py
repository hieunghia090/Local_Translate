import httpx

from app.main import create_app


def make_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("không được lộ", encoding="utf-8")
    return dist


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_serves_index_assets_and_spa_fallback(tmp_path):
    app = create_app(frontend_dist=make_dist(tmp_path))
    async with client(app) as c:
        assert "id=root" in (await c.get("/")).text
        assert "id=root" in (await c.get("/books/abc/chapters/3")).text  # route của SPA
        assert (await c.get("/assets/app.js")).text == "console.log(1)"
        assert (await c.get("/favicon.svg")).text == "<svg/>"
        r = await c.get("/api/v1/khong-co")
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


async def test_no_path_traversal(tmp_path):
    app = create_app(frontend_dist=make_dist(tmp_path))
    async with client(app) as c:
        r = await c.get("/..%2Fsecret.txt")
        assert "không được lộ" not in r.text


async def test_without_build_api_still_works(tmp_path):
    app = create_app(frontend_dist=tmp_path / "missing")
    async with client(app) as c:
        assert (await c.get("/")).status_code == 404
