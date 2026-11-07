import pytest
from fastapi.testclient import TestClient

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRID_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("GRID_PHOTO_DIR", str(tmp_path / "photos"))
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def apply(client, email="ali@gmail.com", **over):
    body = {"name": "Ali Khan", "email": email, "department": "Computer Science", **over}
    return client.post("/api/applications", json=body)


# ---------- site + static ----------
def test_site_serves_html_and_config(client):
    r = client.get("/")
    assert r.status_code == 200 and "G.R.I.D" in r.text
    s = client.get("/api/site").json()
    assert s["applications_open"] is True
    assert "Computer Science" in s["departments"]
    assert client.get("/api/health").json() == {"ok": True}


def test_security_headers(client):
    r = client.get("/api/site")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"


# ---------- applications ----------
def test_apply_ok_then_duplicate(client):
    assert apply(client).status_code == 201
    r = apply(client, email="ALI@gmail.com")  # same email, different case
    assert r.status_code == 409


@pytest.mark.parametrize(
    "over",
    [{"email": "not-an-email"}, {"name": "A"}, {"department": ""}, {"extra": "x"}],
)
def test_apply_validation(client, over):
    assert apply(client, **over).status_code == 422


def test_apply_unknown_department(client):
    assert apply(client, department="Astrology").status_code == 422


def test_apply_closed(client):
    client.patch("/api/admin/settings", json={"applications_open": False}, headers=AUTH)
    assert apply(client).status_code == 403
    assert client.get("/api/site").json()["applications_open"] is False


def test_apply_rate_limited(client):
    for i in range(5):
        assert apply(client, email=f"u{i}@gmail.com").status_code == 201
    assert apply(client, email="u99@gmail.com").status_code == 429


# ---------- admin auth ----------
def test_admin_requires_token(client):
    assert client.get("/api/admin/applications").status_code == 401
    assert client.get("/api/admin/applications", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/admin/applications", headers=AUTH).status_code == 200


def test_admin_disabled_without_token(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "")
    assert client.get("/api/admin/applications", headers=AUTH).status_code == 503


def test_admin_bruteforce_lockout(client):
    for _ in range(10):
        assert client.get("/api/admin/members", headers={"Authorization": "Bearer bad"}).status_code == 401
    assert client.get("/api/admin/members", headers=AUTH).status_code == 429


# ---------- applications admin ----------
def test_application_status_csv_delete(client):
    apply(client, name="=cmd|' /C calc'!A0", email="x@gmail.com")
    row = client.get("/api/admin/applications", headers=AUTH).json()[0]
    assert row["status"] == "new"
    assert client.patch(f"/api/admin/applications/{row['id']}", json={"status": "accepted"}, headers=AUTH).status_code == 200
    assert len(client.get("/api/admin/applications?status=accepted", headers=AUTH).json()) == 1
    csv_text = client.get("/api/admin/applications.csv", headers=AUTH).text
    assert "'=cmd" in csv_text  # formula neutralised
    assert client.delete(f"/api/admin/applications/{row['id']}", headers=AUTH).status_code == 204
    assert client.delete(f"/api/admin/applications/{row['id']}", headers=AUTH).status_code == 404


# ---------- members ----------
def test_members_crud_and_filter(client):
    assert client.get("/api/members").json() == []
    a = client.post(
        "/api/admin/members",
        json={"name": "Sara", "team": "software", "role": "Team lead", "links": {"github": "https://github.com/sara"}},
        headers=AUTH,
    )
    assert a.status_code == 201
    client.post("/api/admin/members", json={"name": "Omar", "team": "rnd", "sort": 5}, headers=AUTH)
    assert [m["name"] for m in client.get("/api/members").json()] == ["Sara", "Omar"]
    assert [m["name"] for m in client.get("/api/members?team=rnd").json()] == ["Omar"]
    assert client.get("/api/members?team=nope").status_code == 422

    mid = a.json()["id"]
    r = client.patch(f"/api/admin/members/{mid}", json={"role": "Member"}, headers=AUTH)
    assert r.json()["role"] == "Member" and r.json()["links"]["github"] == "https://github.com/sara"
    assert client.patch(f"/api/admin/members/{mid}", json={}, headers=AUTH).status_code == 400
    assert client.delete(f"/api/admin/members/{mid}", headers=AUTH).status_code == 204
    assert client.patch("/api/admin/members/9999", json={"role": "x"}, headers=AUTH).status_code == 404


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,hi", "//evil.com", "ftp://x.com"])
def test_member_links_reject_non_http(client, url):
    r = client.post(
        "/api/admin/members", json={"name": "X", "team": "rnd", "links": {"github": url}}, headers=AUTH
    )
    assert r.status_code == 422


def test_member_photo_upload(client):
    mid = client.post("/api/admin/members", json={"name": "P", "team": "comms"}, headers=AUTH).json()["id"]
    bad = client.put(f"/api/admin/members/{mid}/photo", content=b"<html>not an image</html>", headers=AUTH)
    assert bad.status_code == 415
    big = client.put(f"/api/admin/members/{mid}/photo", content=PNG + b"0" * (2 * 1024 * 1024), headers=AUTH)
    assert big.status_code == 413
    ok = client.put(f"/api/admin/members/{mid}/photo", content=PNG, headers=AUTH)
    assert ok.status_code == 200
    path = ok.json()["photo"]
    assert client.get("/" + path).content == PNG
    # replacing removes the old file
    new = client.put(f"/api/admin/members/{mid}/photo", content=PNG, headers=AUTH).json()["photo"]
    assert new != path and client.get("/" + path).status_code == 404
    client.delete(f"/api/admin/members/{mid}", headers=AUTH)
    assert client.get("/" + new).status_code == 404


# ---------- log ----------
def test_log_order_and_validation(client):
    mk = lambda d, t, **kw: client.post(
        "/api/admin/log", json={"date": d, "kind": "Project", "title": t, **kw}, headers=AUTH
    )
    assert mk("2026-09-01", "Older").status_code == 201
    new = mk("2026-11-02", "Newer", result_label="Repo", result_url="https://github.com/x/y")
    assert new.json()["when"] == "Nov 2026"
    assert [e["title"] for e in client.get("/api/log").json()] == ["Newer", "Older"]
    assert mk("2026-11-02", "Bad", result_url="javascript:alert(1)").status_code == 422
    assert mk("not-a-date", "Bad").status_code == 422
    assert client.post("/api/admin/log", json={"date": "2026-01-01", "kind": "Party", "title": "x"}, headers=AUTH).status_code == 422
    lid = new.json()["id"]
    assert client.patch(f"/api/admin/log/{lid}", json={"title": "Renamed"}, headers=AUTH).json()["title"] == "Renamed"
    assert client.delete(f"/api/admin/log/{lid}", headers=AUTH).status_code == 204


# ---------- settings ----------
def test_settings_patch(client):
    r = client.patch(
        "/api/admin/settings",
        json={"handle": "@gridnust", "contact_email": "grid@nust.edu.pk", "departments": ["Computer Science", "Other"]},
        headers=AUTH,
    )
    assert r.status_code == 200
    pub = client.get("/api/site").json()
    assert pub["handle"] == "@gridnust" and pub["departments"] == ["Computer Science", "Other"]
    assert apply(client, department="Other").status_code == 201
    assert client.patch("/api/admin/settings", json={"contact_email": "nope"}, headers=AUTH).status_code == 422
    assert client.patch("/api/admin/settings", json={"departments": []}, headers=AUTH).status_code == 422
