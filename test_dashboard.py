import json
import pytest

from dashboard.app import app, RESULTS_FILE, save_results


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def clear_results():
    if RESULTS_FILE.exists():
        RESULTS_FILE.unlink()
    yield
    if RESULTS_FILE.exists():
        RESULTS_FILE.unlink()


def seed_results():
    data = [
        {
            "id": "aaa",
            "url": "https://api.example.com",
            "timestamp": "2026-07-24T22:00:00",
            "overall_status": "PASS",
            "summary": "ok",
            "checks": [{"name": "manifest", "status": "PASS", "message": "ok"}],
        },
        {
            "id": "bbb",
            "url": "https://bad.example.com",
            "timestamp": "2026-07-24T21:00:00",
            "overall_status": "FAIL",
            "summary": "missing manifest",
            "checks": [{"name": "manifest", "status": "FAIL", "message": "missing"}],
        },
    ]
    save_results(data)


class TestDashboardRoutes:
    def test_index_get(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"x402 Validator Dashboard" in resp.data

    def test_index_post_redirects(self, client):
        resp = client.post("/", data={"url": "https://api.example.com"}, follow_redirects=False)
        assert resp.status_code == 302

    def test_index_with_history(self, client):
        seed_results()
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"api.example.com" in resp.data

    def test_history_api(self, client):
        seed_results()
        resp = client.get("/api/history")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) == 2

    def test_report_exists(self, client):
        seed_results()
        resp = client.get("/report/aaa")
        assert resp.status_code == 200
        assert b"PASS" in resp.data

    def test_report_not_found(self, client):
        resp = client.get("/report/nonexistent")
        assert resp.status_code == 404
        assert b"not found" in resp.data

    def test_api_history_empty(self, client):
        resp = client.get("/api/history")
        assert resp.status_code == 200
        assert json.loads(resp.data) == []
