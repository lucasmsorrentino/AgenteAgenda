"""Tests for integrations.anytype_client helpers that don't hit the network.

Uses httpx.MockTransport so we can simulate the local Anytype API deterministically.
"""

from __future__ import annotations

import httpx
import pytest

from integrations.anytype_client import AnytypeClient


def _make_client(handler):
    """Build an AnytypeClient backed by a MockTransport for the given handler."""
    transport = httpx.MockTransport(handler)
    client = AnytypeClient(base_url="http://fake", api_key="k", space_id="s")
    client._client = httpx.Client(
        transport=transport,
        base_url="http://fake",
        headers=client.headers,
    )
    return client


def test_create_object_posts_expected_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"object": {"id": "obj-123"}})

    client = _make_client(handler)
    obj_id = client.create_object(type_key="tarefa", name="Estudar", description="foo")

    assert obj_id == "obj-123"
    assert "/v1/spaces/s/objects" in captured["url"]
    assert "Estudar" in captured["body"]
    assert "task" in captured["body"]  # type_key 'tarefa' → 'task'


def test_create_object_returns_none_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = _make_client(handler)
    assert client.create_object(type_key="tarefa", name="x") is None


def test_update_object_properties():
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        assert "checkbox" in body
        return httpx.Response(200, json={})

    client = _make_client(handler)
    assert client.update_object_properties("obj-1", [{"key": "done", "checkbox": True}]) is True


def test_delete_object():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        return httpx.Response(200, json={})

    client = _make_client(handler)
    assert client.delete_object("obj-xyz") is True
    assert seen["method"] == "DELETE"


def test_list_objects_with_type_uses_search():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        return httpx.Response(200, json={"data": [{"id": "x", "name": "Y"}]})

    client = _make_client(handler)
    result = client.list_objects(type_key="tarefa", limit=10)
    assert result == [{"id": "x", "name": "Y"}]
    assert "/search" in seen["url"]
    assert seen["method"] == "POST"


def test_set_classification_skips_when_schema_missing(monkeypatch):
    """If the schema doesn't have the property keys, set_classification is a no-op."""
    import integrations.anytype_client as mod
    monkeypatch.setattr(mod, "_SCHEMA", {"properties": {}})

    client = _make_client(lambda r: httpx.Response(500))  # would fail if reached
    assert client.set_classification("obj-1", area="tcc") is False


def test_set_classification_sends_props_when_schema_present(monkeypatch):
    import integrations.anytype_client as mod
    monkeypatch.setattr(
        mod,
        "_SCHEMA",
        {"properties": {
            "area": "area", "prioridade": "prioridade",
            "tags": "tags", "classified_at": "classified_at",
        }},
    )

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={})

    client = _make_client(handler)
    ok = client.set_classification(
        "obj-1",
        area="tcc",
        prioridade="alta",
        tags=["estudo"],
        classified_at="2026-04-16T10:00:00",
    )
    assert ok
    assert "tcc" in captured["body"]
    assert "alta" in captured["body"]
    assert "estudo" in captured["body"]
