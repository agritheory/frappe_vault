# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

"""
Tests for the Vault proxy API.
"""

import json
from unittest.mock import MagicMock, call, patch

import frappe
import pytest

from frappe_vault import vault_proxy
from frappe_vault.vault_client import VaultError


@pytest.fixture
def vault_user(monkeypatch):
	"""Fixture for a non-Administrator user with Vault access."""
	user_email = "vault-test-user@example.com"
	monkeypatch.setattr("frappe.session.user", user_email)
	monkeypatch.setattr("frappe.get_roles", lambda user: ["System Manager", "Employee"])
	return user_email


@pytest.fixture
def guest_user(monkeypatch):
	"""Fixture for a user without Vault access."""
	user_email = "guest@example.com"
	monkeypatch.setattr("frappe.session.user", user_email)
	monkeypatch.setattr("frappe.get_roles", lambda user: ["Guest"])
	return user_email


@pytest.fixture
def proxy_enabled(monkeypatch):
	"""Enable the vault proxy."""
	monkeypatch.setitem(frappe.conf, "vault_proxy_enabled", True)


@pytest.fixture
def proxy_disabled(monkeypatch):
	"""Disable the vault proxy."""
	monkeypatch.setitem(frappe.conf, "vault_proxy_enabled", False)


@pytest.fixture
def mock_activity_log(monkeypatch):
	"""Mock Activity Log and capture what gets logged."""
	logged_docs = []

	def capture_doc(doc_dict):
		mock = MagicMock()
		logged_docs.append(doc_dict)
		return mock

	monkeypatch.setattr("frappe.get_doc", capture_doc)
	return logged_docs


def test_proxy_disabled_by_default(proxy_disabled):
	assert vault_proxy.is_vault_proxy_enabled() is False


def test_proxy_enabled_when_configured(proxy_enabled):
	assert vault_proxy.is_vault_proxy_enabled() is True


def test_default_allowed_roles(proxy_disabled):
	assert vault_proxy.get_vault_allowed_roles() == ["System Manager"]


def test_custom_allowed_roles(monkeypatch):
	custom_roles = ["System Manager", "Vault Admin"]
	monkeypatch.setitem(frappe.conf, "vault_allowed_roles", custom_roles)
	assert vault_proxy.get_vault_allowed_roles() == custom_roles


def test_administrator_always_has_access(monkeypatch, proxy_enabled):
	monkeypatch.setattr("frappe.session.user", "Administrator")
	assert vault_proxy.has_vault_access() is True


def test_user_with_allowed_role_has_access(vault_user, proxy_enabled):
	assert vault_proxy.has_vault_access() is True


def test_user_without_allowed_role_denied(guest_user, proxy_enabled):
	assert vault_proxy.has_vault_access() is False


def test_decorator_throws_when_proxy_disabled(proxy_disabled, vault_user):
	@vault_proxy.require_vault_access
	def test_func():
		return "success"

	with pytest.raises(frappe.PermissionError):
		test_func()


def test_decorator_throws_without_permission(proxy_enabled, guest_user):
	@vault_proxy.require_vault_access
	def test_func():
		return "success"

	with pytest.raises(frappe.PermissionError):
		test_func()


def test_decorator_allows_with_permission(proxy_enabled, vault_user, mock_activity_log):
	@vault_proxy.require_vault_access
	def test_func():
		return "success"

	assert test_func() == "success"


def test_status_when_disabled(proxy_disabled):
	result = vault_proxy.status()
	assert result["proxy_enabled"] is False
	assert result["vault_available"] is None


def test_status_when_enabled_vault_available(proxy_enabled):
	mock_client = MagicMock()
	mock_client.is_available.return_value = True

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		result = vault_proxy.status()

	assert result["proxy_enabled"] is True
	assert result["vault_available"] is True


def test_status_when_enabled_vault_unavailable(proxy_enabled):
	mock_client = MagicMock()
	mock_client.is_available.return_value = False

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		result = vault_proxy.status()

	assert result["proxy_enabled"] is True
	assert result["vault_available"] is False


def test_health_success(proxy_enabled, vault_user, mock_activity_log):
	mock_client = MagicMock()
	mock_client.check_health.return_value = {"initialized": True, "sealed": False}

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		result = vault_proxy.health()

	assert result["success"] is True
	assert result["data"]["initialized"] is True
	assert result["data"]["sealed"] is False


def test_health_failure(proxy_enabled, vault_user, mock_activity_log):
	mock_client = MagicMock()
	mock_client.check_health.side_effect = VaultError("Connection refused")

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		result = vault_proxy.health()

	assert result["success"] is False
	assert "Connection refused" in result["error"]


def test_health_logs_correct_user(proxy_enabled, vault_user, mock_activity_log):
	"""Verify that audit log captures the actual Frappe user, not Administrator."""
	mock_client = MagicMock()
	mock_client.check_health.return_value = {"initialized": True, "sealed": False}

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		vault_proxy.health()

	assert len(mock_activity_log) == 1
	logged_doc = mock_activity_log[0]
	assert logged_doc["doctype"] == "Activity Log"
	assert logged_doc["user"] == vault_user
	assert logged_doc["reference_name"] == vault_user
	assert "health_check" in logged_doc["subject"]


def test_list_secrets_success(proxy_enabled, vault_user, mock_activity_log):
	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_response.json.return_value = {"data": {"keys": ["User/", "Integration/"]}}

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_response

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		result = vault_proxy.list_secrets("frappe")

	assert result["success"] is True
	assert "keys" in result["data"]
	mock_client._make_request.assert_called_once_with("LIST", "/v1/secret/metadata/frappe")


def test_list_secrets_empty(proxy_enabled, vault_user, mock_activity_log):
	mock_response = MagicMock()
	mock_response.status_code = 404

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_response

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		result = vault_proxy.list_secrets("nonexistent")

	assert result["success"] is True
	assert result["data"]["keys"] == []


def test_list_secrets_logs_correct_user(proxy_enabled, vault_user, mock_activity_log):
	"""Verify list_secrets logs the correct user."""
	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_response.json.return_value = {"data": {"keys": []}}

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_response

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		vault_proxy.list_secrets("frappe")

	assert len(mock_activity_log) == 1
	assert mock_activity_log[0]["user"] == vault_user


def test_proxy_request_invalid_path(proxy_enabled, vault_user, mock_activity_log):
	result = vault_proxy.proxy_request("/invalid/path")
	assert result["success"] is False
	assert "Invalid path" in result["error"]


def test_proxy_request_blocked_seal(proxy_enabled, vault_user, mock_activity_log):
	result = vault_proxy.proxy_request("/v1/sys/seal")
	assert result["success"] is False
	assert "not allowed" in result["error"]


def test_proxy_request_blocked_unseal(proxy_enabled, vault_user, mock_activity_log):
	result = vault_proxy.proxy_request("/v1/sys/unseal")
	assert result["success"] is False
	assert "not allowed" in result["error"]


def test_proxy_request_blocked_init(proxy_enabled, vault_user, mock_activity_log):
	result = vault_proxy.proxy_request("/v1/sys/init")
	assert result["success"] is False
	assert "not allowed" in result["error"]


def test_proxy_request_blocked_token_create(proxy_enabled, vault_user, mock_activity_log):
	result = vault_proxy.proxy_request("/v1/auth/token/create")
	assert result["success"] is False
	assert "not allowed" in result["error"]


def test_proxy_request_success(proxy_enabled, vault_user, mock_activity_log):
	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_response.json.return_value = {"data": {"value": "test"}}

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_response

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		result = vault_proxy.proxy_request("/v1/secret/data/myapp/config", "GET")

	assert result["success"] is True
	mock_client._make_request.assert_called_once()


def test_proxy_request_with_data(proxy_enabled, vault_user, mock_activity_log):
	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_response.json.return_value = {"data": {"version": 1}}

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_response

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		result = vault_proxy.proxy_request(
			"/v1/secret/data/myapp/config", "POST", json.dumps({"data": {"key": "value"}})
		)

	assert result["success"] is True
	call_args = mock_client._make_request.call_args
	assert call_args[0][0] == "POST"
	assert call_args[1]["data"] == {"data": {"key": "value"}}


def test_proxy_request_204_response(proxy_enabled, vault_user, mock_activity_log):
	mock_response = MagicMock()
	mock_response.status_code = 204

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_response

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		result = vault_proxy.proxy_request("/v1/secret/metadata/test", "DELETE")

	assert result["success"] is True
	assert result["data"] is None


def test_proxy_request_logs_correct_user(proxy_enabled, vault_user, mock_activity_log):
	"""Verify proxy_request logs the correct user."""
	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_response.json.return_value = {"data": {}}

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_response

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		vault_proxy.proxy_request("/v1/secret/data/test", "GET")

	assert len(mock_activity_log) == 1
	assert mock_activity_log[0]["user"] == vault_user
	content = json.loads(mock_activity_log[0]["content"])
	assert content["action"] == "proxy_request"
	assert content["path"] == "/v1/secret/data/test"


def test_delete_secret_logs_correct_user(proxy_enabled, vault_user, mock_activity_log):
	"""Verify delete_secret logs the correct user."""
	mock_response = MagicMock()
	mock_response.status_code = 204

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_response

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		vault_proxy.delete_secret("frappe/User/test/password")

	assert len(mock_activity_log) == 1
	assert mock_activity_log[0]["user"] == vault_user


def test_health_integration(proxy_enabled, vault_user, mock_activity_log):
	"""Health check should work against real OpenBao."""
	result = vault_proxy.health()
	assert result["success"] is True
	assert "initialized" in result["data"] or "reachable" in result["data"]


def test_list_secrets_integration(proxy_enabled, vault_user, mock_activity_log):
	"""List secrets should work against real OpenBao."""
	result = vault_proxy.list_secrets("frappe")
	assert result["success"] is True
	assert "keys" in result["data"] or result["data"] == {"keys": []}


# ---------------------------------------------------------------------------
# Tests for VaultApiRenderer (the /v1/* page renderer)
# ---------------------------------------------------------------------------

from frappe_vault.vault_api_renderer import VaultApiRenderer


@pytest.fixture
def mock_get_request(monkeypatch):
	"""Attach a minimal mock GET request to frappe.local."""

	class MockRequest:
		method = "GET"
		content_type = "application/json"

		def get_data(self, as_text=False):
			return ""

	req = MockRequest()
	frappe.local.request = req
	return req


def render(path):
	"""Helper: instantiate VaultApiRenderer and call render()."""
	return VaultApiRenderer(path).render()


def test_renderer_requires_proxy_enabled(proxy_disabled, vault_user, mock_get_request):
	"""Renderer returns 403 when vault proxy is disabled."""
	resp = render("v1/sys/health")
	assert resp.status_code == 403
	assert "not enabled" in resp.get_data(as_text=True)


def test_renderer_requires_auth(proxy_enabled, mock_get_request, monkeypatch):
	"""Renderer returns 401 for guest users."""
	monkeypatch.setattr("frappe.session.user", "Guest")
	resp = render("v1/sys/health")
	assert resp.status_code == 401


def test_renderer_requires_permission(
	proxy_enabled, guest_user, mock_get_request, mock_activity_log
):
	"""Renderer returns 403 for users without an allowed role."""
	resp = render("v1/sys/health")
	assert resp.status_code == 403


def test_renderer_blocks_seal(proxy_enabled, vault_user, mock_get_request, mock_activity_log):
	resp = render("v1/sys/seal")
	assert resp.status_code == 403
	assert "not allowed" in resp.get_data(as_text=True)


def test_renderer_blocks_unseal(proxy_enabled, vault_user, mock_get_request, mock_activity_log):
	resp = render("v1/sys/unseal")
	assert resp.status_code == 403


def test_renderer_blocks_token_create(
	proxy_enabled, vault_user, mock_get_request, mock_activity_log
):
	resp = render("v1/auth/token/create")
	assert resp.status_code == 403


def test_renderer_forwards_get_request(
	proxy_enabled, vault_user, mock_get_request, mock_activity_log
):
	"""Renderer proxies valid GET requests to OpenBao and mirrors the response."""
	mock_vault_response = MagicMock()
	mock_vault_response.status_code = 200
	mock_vault_response.json.return_value = {"initialized": True, "sealed": False}

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_vault_response

	with patch("frappe_vault.vault_api_renderer.get_vault_client", return_value=mock_client):
		resp = render("v1/sys/health")

	assert resp.status_code == 200
	assert json.loads(resp.data) == {"initialized": True, "sealed": False}
	mock_client._make_request.assert_called_once_with("GET", "/v1/sys/health", data=None)


def test_renderer_logs_correct_user(
	proxy_enabled, vault_user, mock_get_request, mock_activity_log
):
	"""Renderer records the authenticated Frappe user in the audit log."""
	mock_vault_response = MagicMock()
	mock_vault_response.status_code = 200
	mock_vault_response.json.return_value = {"initialized": True}

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_vault_response

	with patch("frappe_vault.vault_api_renderer.get_vault_client", return_value=mock_client):
		render("v1/sys/health")

	assert len(mock_activity_log) == 1
	assert mock_activity_log[0]["user"] == vault_user


def test_renderer_forwards_post_data(proxy_enabled, vault_user, mock_activity_log, monkeypatch):
	"""Renderer parses the JSON request body and forwards it to OpenBao."""

	class MockPostRequest:
		method = "POST"
		content_type = "application/json"

		def get_data(self, as_text=False):
			return '{"data": {"key": "value"}}'

	frappe.local.request = MockPostRequest()

	mock_vault_response = MagicMock()
	mock_vault_response.status_code = 200
	mock_vault_response.json.return_value = {"data": {"version": 1}}

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_vault_response

	with patch("frappe_vault.vault_api_renderer.get_vault_client", return_value=mock_client):
		resp = render("v1/secret/data/myapp/config")

	assert resp.status_code == 200
	call_args = mock_client._make_request.call_args
	assert call_args[0][0] == "POST"
	assert call_args[0][1] == "/v1/secret/data/myapp/config"
	assert call_args[1]["data"] == {"data": {"key": "value"}}
