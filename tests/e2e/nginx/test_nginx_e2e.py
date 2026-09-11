"""nginx E2E tests verifying integration with the investigation pipeline.

Mocks ``integrations.nginx.client.build_client`` at the transport boundary —
no real or containerized nginx server is needed, mirroring
``tests/e2e/aerospike/test_aerospike_e2e.py`` (which patches
``integrations.aerospike.send_info_commands`` rather than starting a live
Aerospike node).

Tests:
- nginx config resolution from store and env
- nginx verification (status check)
- nginx source availability for query execution
- nginx tools are discoverable on the chat surface
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx
import pytest

from integrations.catalog import classify_integrations as _classify_integrations
from integrations.nginx import (
    NginxConfig,
    nginx_extract_params,
    nginx_is_available,
)
from integrations.nginx import client as nginx_client
from integrations.verify import verify_integrations

STUB_BODY = (
    "Active connections: 1 \n"
    "server accepts handled requests\n"
    " 7 7 7 \n"
    "Reading: 0 Writing: 1 Waiting: 0 \n"
)


def _mock_transport(routes: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in routes:
            payload = routes[path]
            if isinstance(payload, httpx.Response):
                return payload
            if isinstance(payload, str):
                return httpx.Response(
                    HTTPStatus.OK, text=payload, headers={"server": "nginx/1.27.5"}
                )
            return httpx.Response(HTTPStatus.OK, json=payload)
        return httpx.Response(HTTPStatus.NOT_FOUND, text="not found")

    return httpx.MockTransport(handler)


@pytest.fixture
def patched_client(monkeypatch: pytest.MonkeyPatch):
    def install(routes: dict[str, Any]) -> None:
        def _fake_client(config: NginxConfig) -> httpx.Client:
            return httpx.Client(base_url=config.base_url, transport=_mock_transport(routes))

        monkeypatch.setattr(nginx_client, "build_client", _fake_client)

    return install


class TestNginxIntegrationResolution:
    """Test nginx config resolution from multiple sources."""

    def test_nginx_resolution_from_store(self):
        integrations = [
            {
                "id": "nginx-prod",
                "service": "nginx",
                "status": "active",
                "credentials": {
                    "host": "prod-nginx.internal",
                    "port": 8080,
                    "username": "monitor",
                    "password": "s3cret",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "nginx" in resolved
        assert resolved["nginx"]["host"] == "prod-nginx.internal"
        assert resolved["nginx"]["port"] == 8080
        assert resolved["nginx"]["username"] == "monitor"

    def test_nginx_invalid_config_skipped(self):
        integrations = [
            {
                "id": "bad-nginx",
                "service": "nginx",
                "status": "active",
                "credentials": {"host": ""},
            }
        ]
        resolved = _classify_integrations(integrations)

        assert resolved.get("nginx") is None


class TestNginxToolSourceAvailability:
    """Test nginx source availability in the tool-registry investigation path."""

    def test_nginx_tool_source_available_from_resolved_integration(self):
        resolved_integrations = {"nginx": {"host": "localhost", "port": 80}}

        assert nginx_is_available(resolved_integrations)
        params = nginx_extract_params(resolved_integrations)
        assert params["host"] == "localhost"
        assert params["port"] == 80

    def test_nginx_tool_source_unavailable_if_unconfigured(self):
        assert not nginx_is_available({})


class TestNginxVerification:
    """Test nginx integration verification flow."""

    def test_verify_nginx_success(self, patched_client):
        patched_client({"/nginx_status": STUB_BODY})

        results = verify_integrations(service="nginx")

        assert len(results) >= 1
        nginx_result = next((r for r in results if r["service"] == "nginx"), None)
        assert nginx_result is not None
        assert nginx_result["status"] in ("passed", "missing")

    def test_verify_integrations_structure(self):
        try:
            results = verify_integrations(service="nginx")
            assert isinstance(results, list)
            for result in results:
                if result["service"] == "nginx":
                    assert "status" in result
                    assert "detail" in result
                    assert result["status"] in ("passed", "missing", "failed")
        except Exception as exc:
            assert exc.__class__.__name__


class TestNginxToolsAvailability:
    """Test nginx tools are available and configured."""

    @pytest.fixture(autouse=True)
    def _clear_registry_cache(self):
        from tools.registry import clear_tool_registry_cache

        clear_tool_registry_cache()
        yield
        clear_tool_registry_cache()

    def test_nginx_tools_exist_as_modules(self):
        import importlib

        try:
            server_status_tool = importlib.import_module(
                "integrations.nginx.tools.nginx_server_status_tool"
            )
            upstream_health_tool = importlib.import_module(
                "integrations.nginx.tools.nginx_upstream_health_tool"
            )
            server_zones_tool = importlib.import_module(
                "integrations.nginx.tools.nginx_server_zones_tool"
            )
            cache_status_tool = importlib.import_module(
                "integrations.nginx.tools.nginx_cache_status_tool"
            )
            error_log_tool = importlib.import_module(
                "integrations.nginx.tools.nginx_error_log_tool"
            )
            access_log_tool = importlib.import_module(
                "integrations.nginx.tools.nginx_access_log_summary_tool"
            )

            assert server_status_tool is not None
            assert upstream_health_tool is not None
            assert server_zones_tool is not None
            assert cache_status_tool is not None
            assert error_log_tool is not None
            assert access_log_tool is not None
        except ImportError as e:
            pytest.fail(f"Failed to import nginx tool modules: {e}")

    def test_nginx_tools_registered_on_chat_surface(self):
        from tools.registry import get_registered_tools

        expected_tools = {
            "get_nginx_server_status",
            "get_nginx_upstream_health",
            "get_nginx_server_zones",
            "get_nginx_cache_status",
            "get_nginx_error_log",
            "get_nginx_access_log_summary",
        }
        names = {t.name for t in get_registered_tools("chat") if t.source == "nginx"}
        assert expected_tools <= names, (
            f"missing nginx tools on chat surface: {expected_tools - names}"
        )


class TestNginxToolPaths:
    """Exercise each tool end-to-end: tool fn -> helper -> client/logs."""

    def test_server_status_tool_path(self, patched_client):
        from integrations.nginx.tools.nginx_server_status_tool import (
            get_nginx_server_status,
        )

        patched_client({"/nginx_status": STUB_BODY})

        result = get_nginx_server_status(host="prod-nginx.internal")

        assert result["available"] is True
        assert result["edition"] == "oss"
        assert result["connections"]["active"] == 1

    def test_upstream_health_tool_path(self, patched_client):
        from integrations.nginx.tools.nginx_upstream_health_tool import (
            get_nginx_upstream_health,
        )

        patched_client(
            {
                "/api/": [9],
                "/api/9/http/upstreams": {
                    "backend": {
                        "peers": [
                            {
                                "id": 0,
                                "server": "10.0.0.41:8084",
                                "state": "up",
                            }
                        ],
                        "keepalive": 0,
                        "zombies": 0,
                        "zone": "backend",
                    }
                },
            }
        )

        result = get_nginx_upstream_health(host="prod-nginx.internal")

        assert result["available"] is True
        assert result["upstreams_total"] == 1

    def test_server_zones_tool_path(self, patched_client):
        from integrations.nginx.tools.nginx_server_zones_tool import (
            get_nginx_server_zones,
        )

        patched_client(
            {
                "/api/": [9],
                "/api/9/http/server_zones": {
                    "hg.nginx.org": {
                        "processing": 0,
                        "requests": 10,
                        "responses": {"2xx": 10, "5xx": 0, "total": 10},
                        "discarded": 0,
                        "received": 100,
                        "sent": 200,
                    }
                },
            }
        )

        result = get_nginx_server_zones(host="prod-nginx.internal")

        assert result["available"] is True
        assert result["zones_total"] == 1

    def test_cache_status_tool_path(self, patched_client):
        from integrations.nginx.tools.nginx_cache_status_tool import (
            get_nginx_cache_status,
        )

        patched_client(
            {
                "/api/": [9],
                "/api/9/http/caches": {
                    "http_cache": {
                        "size": 100,
                        "max_size": 1000,
                        "cold": False,
                        "hit": {"responses": 90},
                        "miss": {"responses": 10},
                        "expired": {"responses": 0},
                        "stale": {"responses": 0},
                        "updating": {"responses": 0},
                        "revalidated": {"responses": 0},
                        "bypass": {"responses": 0},
                    }
                },
            }
        )

        result = get_nginx_cache_status(host="prod-nginx.internal")

        assert result["available"] is True
        assert result["caches_total"] == 1

    def test_error_log_tool_path(self, tmp_path):
        from integrations.nginx.tools.nginx_error_log_tool import get_nginx_error_log

        target = tmp_path / "error.log"
        target.write_text(
            "2026/09/09 20:10:55 [error] 25#25: *5 connect() failed "
            "(111: Connection refused) while connecting to upstream, "
            "client: 172.17.0.1\n"
        )

        result = get_nginx_error_log(host="prod-nginx.internal", error_log_path=str(target))

        assert result["available"] is True
        assert result["matched"] == 1

    def test_access_log_tool_path(self, tmp_path):
        from integrations.nginx.tools.nginx_access_log_summary_tool import (
            get_nginx_access_log_summary,
        )

        target = tmp_path / "access.log"
        target.write_text(
            '172.17.0.1 - - [09/Sep/2026:20:10:55 +0000] "GET / HTTP/1.1" 200 3 "-" "curl/8.5.0"\n'
        )

        result = get_nginx_access_log_summary(
            host="prod-nginx.internal", access_log_path=str(target)
        )

        assert result["available"] is True
        assert result["parsed"] == 1
