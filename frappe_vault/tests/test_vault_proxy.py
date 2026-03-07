# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

"""
Tests for the Vault proxy API and VaultApiRenderer.

User context is set with frappe.set_user() against real DB users created by
tests/setup.py. The autouse reset_user fixture in conftest.py restores
Administrator after each test.

Activity Log assertions query the real Activity Log table rather than
intercepting frappe.get_doc — this catches any breakage in the logging path
itself and avoids corrupting the frappe.get_doc call for other code in the
same test.
"""

import json
from unittest.mock import MagicMock, patch

import frappe
import pytest

from frappe_vault import vault_proxy
from frappe_vault.vault_api_renderer import VaultApiRenderer
from frappe_vault.vault_client import VaultError

VAULT_ADMIN = "vault-admin@vault.test"
NO_ACCESS = "no-access@vault.test"
DEPLOY_BOT = "deploy-bot@vault.test"


# ---------------------------------------------------------------------------
# Infrastructure fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def proxy_enabled(monkeypatch):
	"""Enable the vault proxy for tests that explicitly need it."""
	monkeypatch.setitem(frappe.conf, "vault_proxy_enabled", True)


@pytest.fixture
def proxy_disabled(monkeypatch):
	"""Disable the vault proxy for tests that verify the disabled path."""
	monkeypatch.setitem(frappe.conf, "vault_proxy_enabled", False)


@pytest.fixture
def mock_get_request():
	"""Attach a minimal mock GET request to frappe.local for VaultApiRenderer tests."""

	class MockGetRequest:
		method = "GET"
		content_type = "application/json"

		def get_data(self, as_text=False):
			return ""

	frappe.local.request = MockGetRequest()
	return frappe.local.request


def _last_activity_log(user, subject_fragment):
	"""Return the most recent Activity Log entry matching user and subject fragment."""
	results = frappe.get_all(
		"Activity Log",
		filters={"user": user, "subject": ["like", f"%{subject_fragment}%"]},
		fields=["user", "subject", "content", "reference_name"],
		order_by="creation desc",
		limit=1,
	)
	return results[0] if results else None


def render(path):
	"""Instantiate VaultApiRenderer and call render()."""
	return VaultApiRenderer(path).render()


# ---------------------------------------------------------------------------
# Feature flag tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Access control tests
# ---------------------------------------------------------------------------


def test_administrator_always_has_access(proxy_enabled):
	frappe.set_user("Administrator")
	assert vault_proxy.has_vault_access() is True


def test_user_with_allowed_role_has_access(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)
	assert vault_proxy.has_vault_access() is True


def test_user_without_allowed_role_denied(proxy_enabled):
	frappe.set_user(NO_ACCESS)
	assert vault_proxy.has_vault_access() is False


# ---------------------------------------------------------------------------
# _kv_path_from_api_path()
# ---------------------------------------------------------------------------


def test_kv_path_from_api_path_data():
	assert vault_proxy._kv_path_from_api_path("/v1/secret/data/foo/bar") == "foo/bar"


def test_kv_path_from_api_path_metadata():
	assert vault_proxy._kv_path_from_api_path("/v1/secret/metadata/foo/bar") == "foo/bar"


def test_kv_path_from_api_path_other():
	assert vault_proxy._kv_path_from_api_path("/v1/sys/health") is None


def test_kv_path_from_api_path_top_level_data():
	assert vault_proxy._kv_path_from_api_path("/v1/secret/data/mykey") == "mykey"


# ---------------------------------------------------------------------------
# _vault_secret_name_for_path()
# ---------------------------------------------------------------------------


def test_vault_secret_name_for_path_not_site_namespaced():
	"""Paths that don't start with frappe/{site}/ always return None."""
	assert vault_proxy._vault_secret_name_for_path("myapp/api_key") is None
	assert vault_proxy._vault_secret_name_for_path("/v1/secret/data/foo") is None


def test_vault_secret_name_for_path_no_doc(monkeypatch):
	"""Site-namespaced path with no matching Vault Secret doc returns None."""
	monkeypatch.setattr(frappe.db, "exists", lambda *a, **kw: None)
	path = f"frappe/{frappe.local.site}/no-such/secret"
	assert vault_proxy._vault_secret_name_for_path(path) is None


def test_vault_secret_name_for_path_with_doc(monkeypatch):
	"""Site-namespaced path with matching Vault Secret doc returns the doc name."""
	relative = "customers/acme/api_key"
	monkeypatch.setattr(frappe.db, "exists", lambda doctype, name: name)
	path = f"frappe/{frappe.local.site}/{relative}"
	result = vault_proxy._vault_secret_name_for_path(path)
	assert result == relative


# ---------------------------------------------------------------------------
# require_secret_permission()
# ---------------------------------------------------------------------------


def test_require_secret_permission_no_doc_vault_access_granted(proxy_enabled, monkeypatch):
	"""No tracked doc + user has vault role → no exception."""
	frappe.set_user(VAULT_ADMIN)
	monkeypatch.setattr(frappe.db, "exists", lambda *a, **kw: None)
	# Should not raise
	vault_proxy.require_secret_permission(f"frappe/{frappe.local.site}/orphan/path", "read")


def test_require_secret_permission_no_doc_no_vault_access(proxy_enabled, monkeypatch):
	"""No tracked doc + user lacks vault role → PermissionError."""
	frappe.set_user(NO_ACCESS)
	monkeypatch.setattr(frappe.db, "exists", lambda *a, **kw: None)
	with pytest.raises(frappe.PermissionError):
		vault_proxy.require_secret_permission(f"frappe/{frappe.local.site}/orphan/path", "read")


def test_require_secret_permission_with_doc_granted(proxy_enabled, monkeypatch):
	"""Tracked doc + frappe.has_permission grants → no exception."""
	relative = "customers/acme/api_key"
	kv_path = f"frappe/{frappe.local.site}/{relative}"
	monkeypatch.setattr(frappe.db, "exists", lambda doctype, name: name)
	monkeypatch.setattr(frappe, "has_permission", lambda *a, **kw: True)
	vault_proxy.require_secret_permission(kv_path, "read")


def test_require_secret_permission_with_doc_denied(proxy_enabled, monkeypatch):
	"""Tracked doc + frappe.has_permission denies → PermissionError."""
	relative = "customers/acme/api_key"
	kv_path = f"frappe/{frappe.local.site}/{relative}"
	monkeypatch.setattr(frappe.db, "exists", lambda doctype, name: name)
	monkeypatch.setattr(frappe, "has_permission", lambda *a, **kw: False)
	with pytest.raises(frappe.PermissionError):
		vault_proxy.require_secret_permission(kv_path, "read")


# ---------------------------------------------------------------------------
# _ensure_vault_secret()
# ---------------------------------------------------------------------------


def test_ensure_vault_secret_non_site_path_is_noop(monkeypatch):
	"""Non-site-namespaced paths are silently ignored."""
	created = []
	monkeypatch.setattr(frappe.db, "exists", lambda *a, **kw: None)
	with patch("frappe_vault.vault_proxy._vault_secret_name_for_path", return_value=None):
		with patch("frappe_vault.vault_proxy.frappe.new_doc") as mock_new_doc:
			vault_proxy._ensure_vault_secret("myapp/api_key")
			mock_new_doc.assert_not_called()


def test_ensure_vault_secret_already_exists_is_noop(monkeypatch):
	"""If a doc already exists, _ensure_vault_secret does nothing."""
	relative = "customers/acme/api_key"
	kv_path = f"frappe/{frappe.local.site}/{relative}"
	with patch("frappe_vault.vault_proxy._vault_secret_name_for_path", return_value=relative):
		with patch("frappe_vault.vault_proxy.frappe.new_doc") as mock_new_doc:
			vault_proxy._ensure_vault_secret(kv_path)
			mock_new_doc.assert_not_called()


def test_ensure_vault_secret_creates_doc_and_folder_chain(monkeypatch):
	"""For a new site-namespaced path, creates folder chain then the secret doc."""
	relative = "customers/acme/api_key"
	kv_path = f"frappe/{frappe.local.site}/{relative}"

	# _ensure_folder_chain is imported locally inside _ensure_vault_secret to
	# avoid a circular import, so patch it at the source module.
	with patch("frappe_vault.vault_proxy._vault_secret_name_for_path", return_value=None), patch(
		"frappe_vault.frappe_vault.doctype.vault_secret.vault_secret._ensure_folder_chain"
	) as mock_chain, patch("frappe_vault.vault_proxy.frappe.new_doc") as mock_new_doc:
		mock_doc = MagicMock()
		mock_new_doc.return_value = mock_doc
		vault_proxy._ensure_vault_secret(kv_path)

	mock_chain.assert_called_once_with(relative)
	mock_new_doc.assert_called_once_with("Vault Secret")
	assert mock_doc.title == "api_key"
	assert mock_doc.path == relative
	assert mock_doc.is_folder == 0
	assert mock_doc.folder == "customers/acme"
	mock_doc.insert.assert_called_once()


# ---------------------------------------------------------------------------
# proxy_request() — permission integration with new helper layer
# ---------------------------------------------------------------------------


def test_proxy_write_calls_ensure_vault_secret(proxy_enabled, monkeypatch):
	"""POST to a KV data path triggers _ensure_vault_secret."""
	frappe.set_user(VAULT_ADMIN)
	relative = f"{frappe.local.site}/myapp/api_key"
	kv_path = f"frappe/{relative}"

	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_response.json.return_value = {}

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_response

	with patch("frappe_vault.vault_proxy._ensure_vault_secret") as mock_ensure, patch(
		"frappe_vault.vault_proxy.require_secret_permission"
	), patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		vault_proxy.proxy_request(f"/v1/secret/data/{kv_path}", "POST", "{}")

	mock_ensure.assert_called_once_with(kv_path)


def test_proxy_get_does_not_call_ensure(proxy_enabled, monkeypatch):
	"""GET does not trigger _ensure_vault_secret."""
	frappe.set_user(VAULT_ADMIN)

	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_response.json.return_value = {}

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_response

	with patch("frappe_vault.vault_proxy._ensure_vault_secret") as mock_ensure, patch(
		"frappe_vault.vault_proxy.require_secret_permission"
	), patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		vault_proxy.proxy_request("/v1/secret/data/frappe/myapp/key", "GET")

	mock_ensure.assert_not_called()


# ---------------------------------------------------------------------------
# Decorator tests
# ---------------------------------------------------------------------------


def test_decorator_throws_when_proxy_disabled(proxy_disabled):
	frappe.set_user(VAULT_ADMIN)

	@vault_proxy.require_vault_access
	def test_func():
		return "success"

	with pytest.raises(frappe.PermissionError):
		test_func()


def test_decorator_throws_without_permission(proxy_enabled):
	frappe.set_user(NO_ACCESS)

	@vault_proxy.require_vault_access
	def test_func():
		return "success"

	with pytest.raises(frappe.PermissionError):
		test_func()


def test_decorator_allows_with_permission(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)

	@vault_proxy.require_vault_access
	def test_func():
		return "success"

	assert test_func() == "success"


# ---------------------------------------------------------------------------
# status() — guest-accessible, no vault client call when proxy disabled
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# health()
# ---------------------------------------------------------------------------


def test_health_success(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)
	mock_client = MagicMock()
	mock_client.check_health.return_value = {"initialized": True, "sealed": False}

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		result = vault_proxy.health()

	assert result["success"] is True
	assert result["data"]["initialized"] is True
	assert result["data"]["sealed"] is False


def test_health_failure(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)
	mock_client = MagicMock()
	mock_client.check_health.side_effect = VaultError("Connection refused")

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		result = vault_proxy.health()

	assert result["success"] is False
	assert "Connection refused" in result["error"]


def test_health_logs_correct_user(proxy_enabled):
	"""Audit log must capture the actual Frappe user, not Administrator."""
	frappe.set_user(VAULT_ADMIN)
	mock_client = MagicMock()
	mock_client.check_health.return_value = {"initialized": True, "sealed": False}

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		vault_proxy.health()

	log = _last_activity_log(VAULT_ADMIN, "health_check")
	assert log is not None
	assert log["user"] == VAULT_ADMIN
	assert log["reference_name"] == VAULT_ADMIN
	assert "health_check" in log["subject"]


# ---------------------------------------------------------------------------
# list_secrets()
# ---------------------------------------------------------------------------


def test_list_secrets_success(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)
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


def test_list_secrets_empty(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)
	mock_response = MagicMock()
	mock_response.status_code = 404

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_response

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		result = vault_proxy.list_secrets("nonexistent")

	assert result["success"] is True
	assert result["data"]["keys"] == []


def test_list_secrets_logs_correct_user(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)
	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_response.json.return_value = {"data": {"keys": []}}

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_response

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		vault_proxy.list_secrets("frappe")

	log = _last_activity_log(VAULT_ADMIN, "list_secrets")
	assert log is not None
	assert log["user"] == VAULT_ADMIN


# ---------------------------------------------------------------------------
# proxy_request() — path validation and blocked paths
# ---------------------------------------------------------------------------


def test_proxy_request_invalid_path(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)
	result = vault_proxy.proxy_request("/invalid/path")
	assert result["success"] is False
	assert "Invalid path" in result["error"]


def test_proxy_request_blocked_seal(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)
	result = vault_proxy.proxy_request("/v1/sys/seal")
	assert result["success"] is False
	assert "not allowed" in result["error"]


def test_proxy_request_blocked_unseal(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)
	result = vault_proxy.proxy_request("/v1/sys/unseal")
	assert result["success"] is False
	assert "not allowed" in result["error"]


def test_proxy_request_blocked_init(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)
	result = vault_proxy.proxy_request("/v1/sys/init")
	assert result["success"] is False
	assert "not allowed" in result["error"]


def test_proxy_request_blocked_token_create(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)
	result = vault_proxy.proxy_request("/v1/auth/token/create")
	assert result["success"] is False
	assert "not allowed" in result["error"]


def test_proxy_request_success(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)
	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_response.json.return_value = {"data": {"value": "test"}}

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_response

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		result = vault_proxy.proxy_request("/v1/secret/data/myapp/config", "GET")

	assert result["success"] is True
	mock_client._make_request.assert_called_once()


def test_proxy_request_with_data(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)
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


def test_proxy_request_204_response(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)
	mock_response = MagicMock()
	mock_response.status_code = 204

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_response

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		result = vault_proxy.proxy_request("/v1/secret/metadata/test", "DELETE")

	assert result["success"] is True
	assert result["data"] is None


def test_proxy_request_logs_correct_user(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)
	mock_response = MagicMock()
	mock_response.status_code = 200
	mock_response.json.return_value = {"data": {}}

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_response

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		vault_proxy.proxy_request("/v1/secret/data/test", "GET")

	log = _last_activity_log(VAULT_ADMIN, "proxy_request")
	assert log is not None
	assert log["user"] == VAULT_ADMIN
	content = json.loads(log["content"])
	assert content["action"] == "proxy_request"
	assert content["path"] == "/v1/secret/data/test"


# ---------------------------------------------------------------------------
# delete_secret()
# ---------------------------------------------------------------------------


def test_delete_secret_logs_correct_user(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)
	mock_response = MagicMock()
	mock_response.status_code = 204

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_response

	with patch("frappe_vault.vault_proxy.get_vault_client", return_value=mock_client):
		vault_proxy.delete_secret("frappe/User/test/password")

	log = _last_activity_log(VAULT_ADMIN, "delete_secret")
	assert log is not None
	assert log["user"] == VAULT_ADMIN


# ---------------------------------------------------------------------------
# Live integration tests (require real OpenBao)
# ---------------------------------------------------------------------------


def test_health_integration(proxy_enabled):
	"""Health check against a real OpenBao instance."""
	frappe.set_user(VAULT_ADMIN)
	result = vault_proxy.health()
	assert result["success"] is True
	assert "initialized" in result["data"]


def test_list_secrets_integration(proxy_enabled):
	"""List secrets against a real OpenBao instance."""
	frappe.set_user(VAULT_ADMIN)
	result = vault_proxy.list_secrets("frappe")
	assert result["success"] is True
	assert "keys" in result["data"]


# ---------------------------------------------------------------------------
# VaultApiRenderer tests
# ---------------------------------------------------------------------------


def test_renderer_requires_proxy_enabled(proxy_disabled, mock_get_request):
	frappe.set_user(VAULT_ADMIN)
	resp = render("v1/sys/health")
	assert resp.status_code == 403
	assert "not enabled" in resp.get_data(as_text=True)


def test_renderer_requires_auth(proxy_enabled, mock_get_request):
	frappe.set_user("Guest")
	resp = render("v1/sys/health")
	assert resp.status_code == 401


def test_renderer_requires_permission(proxy_enabled, mock_get_request):
	frappe.set_user(NO_ACCESS)
	resp = render("v1/sys/health")
	assert resp.status_code == 403


def test_renderer_blocks_seal(proxy_enabled, mock_get_request):
	frappe.set_user(VAULT_ADMIN)
	resp = render("v1/sys/seal")
	assert resp.status_code == 403
	assert "not allowed" in resp.get_data(as_text=True)


def test_renderer_blocks_unseal(proxy_enabled, mock_get_request):
	frappe.set_user(VAULT_ADMIN)
	resp = render("v1/sys/unseal")
	assert resp.status_code == 403


def test_renderer_blocks_token_create(proxy_enabled, mock_get_request):
	frappe.set_user(VAULT_ADMIN)
	resp = render("v1/auth/token/create")
	assert resp.status_code == 403


def test_renderer_forwards_get_request(proxy_enabled, mock_get_request):
	frappe.set_user(VAULT_ADMIN)
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


def test_renderer_logs_correct_user(proxy_enabled, mock_get_request):
	frappe.set_user(VAULT_ADMIN)
	mock_vault_response = MagicMock()
	mock_vault_response.status_code = 200
	mock_vault_response.json.return_value = {"initialized": True}

	mock_client = MagicMock()
	mock_client._make_request.return_value = mock_vault_response

	with patch("frappe_vault.vault_api_renderer.get_vault_client", return_value=mock_client):
		render("v1/sys/health")

	log = _last_activity_log(VAULT_ADMIN, "health_check")
	assert log is not None
	assert log["user"] == VAULT_ADMIN


def test_renderer_forwards_post_data(proxy_enabled):
	frappe.set_user(VAULT_ADMIN)

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
