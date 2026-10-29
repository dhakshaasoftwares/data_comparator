import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(data_root):
    from app.main import app

    with TestClient(app) as c:
        yield c


def _register(client, email, plan=None):
    r = client.post("/api/auth/register", json={"email": email, "password": "password123", "organization": "Acme"})
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    if plan:
        assert client.post("/api/billing/plan", json={"plan": plan}, headers=h).status_code == 200
    return h


def _wait(client, h, run_id):
    for _ in range(120):
        run = client.get(f"/api/runs/{run_id}", headers=h).json()
        if run["status"] in ("completed", "failed"):
            return run
        time.sleep(0.25)
    raise AssertionError("run did not finish")


def test_requires_auth(client):
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/api/billing/plans").status_code == 200


def test_demo_account_and_run(client):
    r = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "demo1234"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    job = client.get("/api/jobs", headers=h).json()[0]
    run = client.post(f"/api/jobs/{job['id']}/run", headers=h).json()
    run = _wait(client, h, run["id"])
    assert run["status"] == "completed"
    assert run["result"]["summary"]["tables"] == 3
    dash = client.get("/api/dashboard", headers=h).json()
    assert dash["kpis"]["tables_tracked"] == 3


def test_tenant_isolation_and_secrets(client):
    a = _register(client, "a@example.com")
    b = _register(client, "b@example.com")
    conn = client.post(
        "/api/connections",
        headers=a,
        json={
            "name": "snow",
            "type": "snowflake",
            "config": {"account": "x", "username": "u", "password": "s3cret", "warehouse": "w", "database": "d"},
        },
    ).json()
    assert "password" not in conn["config"] and conn["secrets_set"] == ["password"]
    assert client.get(f"/api/connections/{conn['id']}/tables", headers=b).status_code == 404
    assert all(c["id"] != conn["id"] for c in client.get("/api/connections", headers=b).json())
    # updating with a blank secret keeps the stored one
    upd = client.put(
        f"/api/connections/{conn['id']}",
        headers=a,
        json={"name": "snow2", "type": "snowflake", "config": {"account": "x", "username": "u", "password": ""}},
    ).json()
    assert upd["secrets_set"] == ["password"]


def test_plan_limits(client):
    h = _register(client, "free@example.com", plan="free")
    for i in range(2):
        r = client.post(
            "/api/connections",
            headers=h,
            json={"name": f"c{i}", "type": "files", "config": {"base_path": "demo/spark_lake"}},
        )
        assert r.status_code == 200
    r = client.post("/api/connections", headers=h, json={"name": "c3", "type": "files", "config": {"base_path": "x"}})
    assert r.status_code == 402
    ids = [c["id"] for c in client.get("/api/connections", headers=h).json()]
    job = {
        "source_connection_id": ids[0],
        "target_connection_id": ids[1],
        "tables": [
            {"source_table": "products", "target_table": "products", "rules": [{"type": "not_null", "column": "sku"}]}
        ],
    }
    r = client.post("/api/jobs", headers=h, json={"name": "j", "config": job})
    assert r.status_code == 402  # quality rules need a paid plan
    job["tables"][0]["rules"] = []
    r = client.post("/api/jobs", headers=h, json={"name": "j", "config": job})
    assert r.status_code == 200
    run = _wait(client, h, client.post(f"/api/jobs/{r.json()['id']}/run", headers=h).json()["id"])
    assert run["status"] == "completed" and run["score"] == 100


def test_job_validation_errors(client):
    h = _register(client, "v@example.com")
    r = client.post("/api/jobs", headers=h, json={"name": "j", "config": {"source_connection_id": 1}})
    assert r.status_code == 422
    r = client.post(
        "/api/jobs",
        headers=h,
        json={
            "name": "j",
            "config": {
                "source_connection_id": 1,
                "target_connection_id": 2,
                "tables": [{"source_table": "a", "target_table": "b"}],
            },
        },
    )
    assert r.status_code == 404  # demo connections belong to another organisation


def test_viewer_cannot_edit(client):
    admin = _register(client, "owner@example.com")
    r = client.post(
        "/api/users", headers=admin, json={"email": "view@example.com", "password": "password123", "role": "viewer"}
    )
    assert r.status_code == 200
    tok = client.post("/api/auth/login", json={"email": "view@example.com", "password": "password123"}).json()["token"]
    viewer = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/jobs", headers=viewer).status_code == 200
    r = client.post("/api/connections", headers=viewer, json={"name": "x", "type": "files", "config": {}})
    assert r.status_code == 403


def test_duplicate_recommend_and_detect_key(client):
    r = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "demo1234"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    job = client.get("/api/jobs", headers=h).json()[-1]
    copy = client.post(f"/api/jobs/{job['id']}/duplicate", headers=h).json()
    assert copy["name"].endswith("(copy)") and copy["config"] == job["config"]
    src, tgt = job["config"]["source_connection_id"], job["config"]["target_connection_id"]
    rec = client.get(f"/api/preview/recommendations?source_connection_id={src}&target_connection_id={tgt}", headers=h)
    assert rec.status_code == 200 and rec.json()["reasons"]
    key = client.post(
        "/api/preview/detect-key", headers=h, json={"source_connection_id": tgt, "source_table": "orders"}
    )
    assert key.json() == {"key_columns": ["order_id"], "how": "detected"}
    bad = client.get(f"/api/connections/{src}/columns?table=NOPE", headers=h)
    assert bad.status_code == 400 and "not found" in bad.json()["detail"]


def test_run_reports_previous_score(client):
    r = client.post("/api/auth/login", json={"email": "demo@example.com", "password": "demo1234"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    job = client.get("/api/jobs", headers=h).json()[-1]
    first = _wait(client, h, client.post(f"/api/jobs/{job['id']}/run", headers=h).json()["id"])
    second = _wait(client, h, client.post(f"/api/jobs/{job['id']}/run", headers=h).json()["id"])
    assert second["previous_score"] == first["score"]
    assert second["result"]["insights"]
