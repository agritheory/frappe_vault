# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

"""
Frappe API proxy for OpenBao.

This module provides whitelisted API methods that proxy requests to OpenBao
after checking Frappe permissions. External tools authenticate via Frappe's
OAuth provider and call these endpoints - they never interact with OpenBao directly.
"""

import json
from functools import wraps
from typing import Any

import frappe
from frappe import _

from frappe_vault.vault_client import VaultError, get_vault_client


def is_vault_proxy_enabled() -> bool:
	"""Check if the Vault proxy API is enabled."""
	return bool(frappe.conf.get("vault_proxy_enabled", False))


def is_vault_secrets_api_enabled() -> bool:
	"""Check if the Vault Secrets doctype UI and API are enabled.

	When this is not set (the default), the vault operates purely as a local
	secret store for internal Frappe use — password fields, monkey-patched auth,
	etc. No access to the Vault Secret doctype or its CRUD API is permitted,
	even for Administrator.

	Set ``vault_secrets_api_enabled: true`` (canonical) or the legacy key
	``enable_vault_secrets: true`` in ``site_config.json`` to allow users with
	appropriate roles to manage secrets through the desk UI and API.
	"""
	return bool(
		frappe.conf.get("vault_secrets_api_enabled") or frappe.conf.get("enable_vault_secrets")
	)


def get_vault_allowed_roles() -> list[str]:
	"""Get the list of roles allowed to access Vault proxy."""
	default_roles = ["System Manager"]
	return frappe.conf.get("vault_allowed_roles", default_roles)


def has_vault_access() -> bool:
	"""Check if the current user has access to Vault proxy."""
	if frappe.session.user == "Administrator":
		return True

	allowed_roles = get_vault_allowed_roles()
	user_roles = frappe.get_roles(frappe.session.user)

	return bool(set(allowed_roles) & set(user_roles))


def require_vault_access(func):
	"""Decorator to require Vault proxy access."""

	@wraps(func)
	def wrapper(*args, **kwargs):
		if not is_vault_proxy_enabled():
			frappe.throw(_("Vault proxy is not enabled"), frappe.PermissionError)

		if not has_vault_access():
			frappe.throw(_("You do not have permission to access Vault"), frappe.PermissionError)

		return func(*args, **kwargs)

	return wrapper


def log_vault_access(action: str, path: str, success: bool, details: str | None = None) -> None:
	"""Log Vault access using Frappe's Activity Log for audit purposes."""
	try:
		frappe.get_doc(
			{
				"doctype": "Activity Log",
				"user": frappe.session.user,
				"subject": f"Vault {action}: {path}",
				"content": json.dumps(
					{
						"action": action,
						"path": path,
						"success": success,
						"details": details,
					}
				),
				"reference_doctype": "User",
				"reference_name": frappe.session.user,
			}
		).insert(ignore_permissions=True)
	except Exception:
		# Don't fail the request if logging fails
		pass


@frappe.whitelist()
@require_vault_access
def health() -> dict[str, Any]:
	"""
	Check OpenBao health status.

	Returns:
	    dict: Health status including initialized, sealed, and version info
	"""
	try:
		client = get_vault_client()
		health_data = client.check_health()
		log_vault_access("health_check", "/v1/sys/health", True)
		return {"success": True, "data": health_data}
	except VaultError as e:
		log_vault_access("health_check", "/v1/sys/health", False, str(e))
		return {"success": False, "error": str(e)}


@frappe.whitelist()
@require_vault_access
def list_secrets(path: str = "frappe") -> dict[str, Any]:
	"""
	List secrets at a given path.

	Args:
	    path: The path to list (default: "frappe")

	Returns:
	    dict: List of keys at the path
	"""
	try:
		client = get_vault_client()
		# Use metadata endpoint for listing
		api_path = f"/v1/secret/metadata/{path}"
		response = client._make_request("LIST", api_path)

		if response.status_code == 404:
			return {"success": True, "data": {"keys": []}}

		if response.status_code != 200:
			raise VaultError(f"Failed to list secrets: {response.status_code}")

		data = response.json()
		log_vault_access("list_secrets", api_path, True)
		return {"success": True, "data": data.get("data", {})}

	except VaultError as e:
		log_vault_access("list_secrets", f"/v1/secret/metadata/{path}", False, str(e))
		return {"success": False, "error": str(e)}


@frappe.whitelist()
@require_vault_access
def get_secret_metadata(path: str) -> dict[str, Any]:
	"""
	Get metadata for a secret (without revealing the value).

	Args:
	    path: Full path to the secret (e.g., "frappe/User/admin/password")

	Returns:
	    dict: Secret metadata including version info and timestamps
	"""
	try:
		client = get_vault_client()
		api_path = f"/v1/secret/metadata/{path}"
		response = client._make_request("GET", api_path)

		if response.status_code == 404:
			return {"success": False, "error": "Secret not found"}

		if response.status_code != 200:
			raise VaultError(f"Failed to get metadata: {response.status_code}")

		data = response.json()
		log_vault_access("get_metadata", api_path, True)
		return {"success": True, "data": data.get("data", {})}

	except VaultError as e:
		log_vault_access("get_metadata", f"/v1/secret/metadata/{path}", False, str(e))
		return {"success": False, "error": str(e)}


@frappe.whitelist()
@require_vault_access
def delete_secret(path: str) -> dict[str, Any]:
	"""
	Delete a secret.

	Args:
	    path: Full path to the secret (e.g., "frappe/User/admin/password")

	Returns:
	    dict: Success status
	"""
	try:
		client = get_vault_client()
		# Delete all versions (metadata delete)
		api_path = f"/v1/secret/metadata/{path}"
		response = client._make_request("DELETE", api_path)

		if response.status_code not in (200, 204, 404):
			raise VaultError(f"Failed to delete secret: {response.status_code}")

		log_vault_access("delete_secret", api_path, True)
		return {"success": True}

	except VaultError as e:
		log_vault_access("delete_secret", f"/v1/secret/metadata/{path}", False, str(e))
		return {"success": False, "error": str(e)}


@frappe.whitelist()
@require_vault_access
def proxy_request(path: str, method: str = "GET", data: str | None = None) -> dict[str, Any]:
	"""
	Generic proxy for OpenBao API requests.

	This is a lower-level API for advanced use cases. Use the specific
	methods (health, list_secrets, etc.) when possible.

	Args:
	    path: API path (e.g., "/v1/secret/data/myapp/config")
	    method: HTTP method (GET, POST, PUT, DELETE, LIST)
	    data: JSON string of request body for POST/PUT

	Returns:
	    dict: Response from OpenBao
	"""
	# Validate path - must start with /v1/
	if not path.startswith("/v1/"):
		return {"success": False, "error": "Invalid path - must start with /v1/"}

	# Block access to sensitive system endpoints
	blocked_paths = [
		"/v1/sys/seal",
		"/v1/sys/unseal",
		"/v1/sys/init",
		"/v1/sys/rekey",
		"/v1/sys/rotate",
		"/v1/auth/token/create",
		"/v1/auth/token/revoke",
	]
	for blocked in blocked_paths:
		if path.startswith(blocked):
			return {"success": False, "error": f"Access to {blocked} is not allowed through proxy"}

	try:
		client = get_vault_client()

		parsed_data = None
		if data:
			parsed_data = json.loads(data) if isinstance(data, str) else data

		response = client._make_request(method, path, data=parsed_data)

		log_vault_access("proxy_request", path, True, f"method={method}")

		# Handle different response types
		if response.status_code == 204:
			return {"success": True, "data": None}

		try:
			return {"success": True, "data": response.json()}
		except json.JSONDecodeError:
			return {"success": True, "data": response.text}

	except VaultError as e:
		log_vault_access("proxy_request", path, False, str(e))
		return {"success": False, "error": str(e)}


def prevent_tag_delete_if_used_on_secret(doc, method=None):
	"""Block deletion of a Frappe Tag that is applied to any Vault Secret.

	Registered as a ``before_delete`` doc_event for the ``Tag`` doctype in
	hooks.py. Frappe stores document tags in ``tabDocument User Tag``; we
	query that table to detect usage before allowing the delete.
	"""
	count = frappe.db.count("Document User Tag", {"tag": doc.name, "document_type": "Vault Secret"})
	if count:
		frappe.throw(
			frappe._("Cannot delete tag '{0}' — it is used on {1} Vault Secret(s).").format(
				doc.name, count
			),
			frappe.ValidationError,
		)


@frappe.whitelist()
def reset_list_user_settings(doctype: str) -> None:
	"""Replace (not merge) the list-view user settings for *doctype* with an empty object.

	Frappe's built-in ``frappe.model.utils.user_settings.save`` always *merges*
	the supplied dict into the existing cached settings, so passing ``{}`` is a
	no-op.  Calling ``update_user_settings`` with ``for_update=True`` bypasses
	the merge and does a direct replacement, which is what the test fixtures need
	to guarantee a clean filter state before each test.
	"""
	from frappe.model.utils.user_settings import update_user_settings

	update_user_settings(doctype, "{}", for_update=True)


@frappe.whitelist(allow_guest=True)
def status() -> dict[str, Any]:
	"""
	Check Vault feature flags and availability (no auth required).

	Returns:
	    dict: Status including proxy and secrets API enabled flags
	"""
	proxy_enabled = is_vault_proxy_enabled()
	return {
		"proxy_enabled": proxy_enabled,
		"secrets_api_enabled": is_vault_secrets_api_enabled(),
		"vault_available": get_vault_client().is_available() if proxy_enabled else None,
	}
