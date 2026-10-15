import pytest
import json

def test_stream_endpoint_streaming(client):
    payload = {
        "incident_data": {
            "incident_id": 999123,
            "first_affected_service": "test-service",
            "trace_id": "test-stream-trace",
            "root_cause_candidate": {
                "service": "test-service",
                "level": "ERROR",
                "raw_message": "Test streaming error message"
            },
            "cascade_flow": []
        },
        "force_refresh": True
    }
    with client.stream("POST", "/rca/analyze/stream", json=payload) as response:
        assert response.status_code == 200
        chunks = list(response.iter_text())
        assert len(chunks) > 0
        full_text = "".join(chunks)
        assert len(full_text) > 0

