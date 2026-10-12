import pytest
from datetime import datetime
from src.parser import extract_template, parse_raw_log

def test_extract_template_replaces_parameters():
    # IP address
    assert extract_template("User connected from 192.168.1.100") == "User connected from <*>"
    # UUID
    assert extract_template("Transaction 123e4567-e89b-12d3-a456-426614174000 failed") == "Transaction <*> failed"
    # Hex
    assert extract_template("Memory fault at address 0xdeadbeef") == "Memory fault at address <*>"
    # Numbers
    assert extract_template("Batch processed 500 items in 42 ms") == "Batch processed <*> items in <*> ms"

def test_parse_raw_log_json():
    json_log = (
        '{"timestamp": "2026-10-09 12:00:00", "level": "ERROR", "service": "payment-service", '
        '"trace_id": "tr-999", "message": "Payment gateway timed out", "user_id": "u-42", "client_ip": "10.0.0.1"}'
    )
    parsed = parse_raw_log(json_log)
    assert parsed is not None
    assert parsed["service_name"] == "payment-service"
    assert parsed["log_level"] == "ERROR"
    assert parsed["trace_id"] == "tr-999"
    assert parsed["user_id"] == "u-42"
    assert parsed["client_ip"] == "10.0.0.1"
    assert "timed out" in parsed["raw_message"]

def test_parse_raw_log_bracket_format():
    log_line = "2026-10-09 12:30:15 [ERROR] [auth-service] [trace_id=tr-abc123] [user_id=usr-55] Invalid JWT token"
    parsed = parse_raw_log(log_line)
    assert parsed is not None
    assert parsed["log_level"] == "ERROR"
    assert parsed["service_name"] == "auth-service"
    assert parsed["trace_id"] == "tr-abc123"
    assert parsed["user_id"] == "usr-55"
    assert "Invalid JWT token" in parsed["raw_message"]

def test_parse_raw_log_empty_and_spaces():
    assert parse_raw_log("") is None
    assert parse_raw_log("   \n\t   ") is None

def test_parse_raw_log_malformed_json_fallback():
    # Невалидный JSON должен парситься или не падать с исключением
    line = '{"service": "broken", invalid'
    # Не должен падать с необработанным исключением
    parsed = parse_raw_log(line)
    # Если не текстовый лог с таймстемпом, возвращается None
    assert parsed is None or isinstance(parsed, dict)
