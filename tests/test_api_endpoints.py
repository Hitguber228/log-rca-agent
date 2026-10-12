import pytest

def test_serve_index(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")

def test_auth_status(client):
    response = client.get("/api/auth/status")
    assert response.status_code == 200
    data = response.json()
    assert "is_bound" in data
    assert "has_token" in data

def test_system_health_summary(client):
    response = client.get("/api/system/health-summary")
    assert response.status_code == 200
    data = response.json()
    assert "total_logs" in data
    assert "total_errors" in data
    assert "db_connected" in data
    assert data["db_connected"] is True

def test_services_list(client):
    response = client.get("/api/logs/services")
    assert response.status_code == 200
    data = response.json()
    assert "services" in data
    assert isinstance(data["services"], list)

def test_ingest_single_log(client):
    payload = {
        "service": "test-service",
        "level": "INFO",
        "message": "User logged in successfully",
        "trace_id": "test-trace-12345",
        "client_ip": "127.0.0.1"
    }
    response = client.post("/api/v1/ingest", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "event_id" in data
    assert isinstance(data["event_id"], int)

def test_search_logs(client):
    response = client.get("/api/logs/search?page=1&limit=20")
    assert response.status_code == 200
    data = response.json()
    assert "events" in data
    assert "total_events" in data
    assert isinstance(data["events"], list)

def test_rca_incidents(client):
    response = client.get("/rca/incidents")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
