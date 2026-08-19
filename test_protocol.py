import json
from urllib.parse import quote

from web.protocol import parse_arguments, parse_scalar


def test_scalar_types():
    assert parse_scalar("true") is True
    assert parse_scalar("false") is False
    assert parse_scalar("null") is None
    assert parse_scalar("123") == 123
    assert parse_scalar("10.25") == 10.25
    assert parse_scalar("Part") == "Part"


def test_complex_json_and_url_encoding():
    encoded = quote(json.dumps({"properties": {"Anchored": True}, "items": [1, 2]}))
    decoded = __import__("urllib.parse", fromlist=["unquote"]).unquote(encoded)
    result = parse_arguments(decoded, {"name": "Thing"}, set())
    assert result["properties"]["Anchored"] is True
    assert result["name"] == "Thing"
