import io
import pytest

def test_upload_forbidden_extension(client):
    file_content = b"malicious binary script content"
    files = {"file": ("malicious.exe", file_content, "application/octet-stream")}
    response = client.post("/logs/upload", files=files)
    assert response.status_code == 400
    assert "Недопустимый формат файла" in response.json().get("detail", "")

def test_upload_allowed_extension(client):
    file_content = b"2026-10-09 12:00:00 [INFO] [api-gateway] Healthy check ok\n"
    files = {"file": ("test_logs.log", file_content, "text/plain")}
    response = client.post("/logs/upload", files=files)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["filename"] == "test_logs.log"

def test_log_source_path_traversal_blocked(client):
    response = client.post("/api/settings/log-source", json={"file_path": "../../etc/passwd"})
    assert response.status_code == 400
    assert "содержит переход" in response.json().get("detail", "")

def test_log_source_windows_system_dir_blocked(client):
    response = client.post("/api/settings/log-source", json={"file_path": "C:\\Windows\\System32\\test.log"})
    assert response.status_code == 400
    assert "системным директориям" in response.json().get("detail", "")

def test_pin_request_rate_limiting(client):
    test_chat_id = "test_flood_user_99999"
    # Первые 5 запросов должны пройти успешно
    for _ in range(5):
        resp = client.post("/api/auth/request-pin", json={"chat_id": test_chat_id})
        assert resp.status_code == 200

    # 6-й запрос должен вернуть 429 Too Many Requests
    excess_resp = client.post("/api/auth/request-pin", json={"chat_id": test_chat_id})
    assert excess_resp.status_code == 429
    assert "Слишком много запросов" in excess_resp.json().get("detail", "")

def test_cors_middleware_configured(client):
    response = client.get("/", headers={"Origin": "http://localhost:3000"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
