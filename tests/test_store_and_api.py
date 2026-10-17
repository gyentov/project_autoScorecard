import io

import pytest
from fastapi.testclient import TestClient

from autoscorecard.config import load_settings
from autoscorecard.store import ProjectStore, safe_filename


def test_config_file_loads():
    s = load_settings()
    assert "offline" in s.providers
    assert s.fallback_chain[-1] == "offline"


def test_safe_filename():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("loan data (2024).csv") == "loan_data_2024_.csv"


def test_store_roundtrip(tmp_path):
    store = ProjectStore(tmp_path)
    p = store.create("Kredit Mikro")
    p.save_upload("loans.csv", io.BytesIO(b"id,bad\n1,0\n"))
    p.audit("prepper", "drop_column", {"column": "x"}, reason="IV < 0.02")
    again = store.get(p.id)
    assert again.meta.name == "Kredit Mikro"
    assert [f.name for f in again.raw_files()] == ["loans.csv"]
    actions = [r["action"] for r in again.audit_log()]
    assert actions == ["project_created", "upload", "drop_column"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    from autoscorecard.server import app as appmod
    monkeypatch.setattr(appmod, "store", ProjectStore(tmp_path))
    return TestClient(appmod.app)


def test_api_flow(client):
    assert client.get("/api/health").json()["ok"]
    assert any(p["name"] == "offline" and p["available"]
               for p in client.get("/api/llm/status").json()["providers"])
    pid = client.post("/api/projects", json={"name": "Demo"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/upload",
                    files=[("files", ("a.csv", b"id\n1\n", "text/csv"))])
    assert r.json() == {"saved": ["a.csv"]}
    bad = client.post(f"/api/projects/{pid}/upload",
                      files=[("files", ("x.exe", b"MZ", "application/octet-stream"))])
    assert bad.status_code == 400
    assert client.post("/api/projects", json={"name": "F", "model_type": "fraud"}).status_code == 400
