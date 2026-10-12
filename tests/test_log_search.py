import pytest
from src.log_search import parse_natural_search_query

def test_parse_natural_search_query_empty():
    assert parse_natural_search_query("") == {}
    assert parse_natural_search_query("   ") == {}

def test_parse_natural_search_query_ip():
    res = parse_natural_search_query("логи с ip 192.168.1.50 за сегодня")
    assert res["client_ip"] == "192.168.1.50"
    assert res["time_from"] is not None

def test_parse_natural_search_query_user_and_trace():
    res = parse_natural_search_query("пользователь user_42 с trace_id abc-123 упал с ошибкой")
    assert res["user_id"] == "user_42"
    assert res["trace_id"] == "abc-123"
    assert "ERROR" in res["levels"]

def test_parse_natural_search_query_known_service():
    res = parse_natural_search_query("ошибки auth-service за 1 час")
    assert res["service"] == "auth-service"
    assert "ERROR" in res["levels"]
    assert res["time_from"] is not None

def test_parse_natural_search_query_level_synonyms():
    res1 = parse_natural_search_query("критическая авария в payment-service")
    assert "CRITICAL" in res1["levels"]
    assert res1["service"] == "payment-service"

    res2 = parse_natural_search_query("предупреждение в nginx")
    assert "WARN" in res2["levels"]
    assert res2["service"] == "nginx"
