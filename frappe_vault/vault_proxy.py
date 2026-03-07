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
	"""Check if the current user has broad Vault proxy access via role.

	Used as the gate for list and health endpoints, and as the fallback for
	proxy paths that don't map to a tracked Vault Secret document.
	"""
	if frappe.session.user == "Administrator":
		return True

	allowed_roles = get_vault_allowed_roles()
	user_roles = frappe.get_roles(frappe.session.user)

	return bool(set(allowed_roles) & set(user_roles))


def require_vault_access(func):
	"""Decorator to require broad Vault proxy access (role-based)."""

	@wraps(func)
	def wrapper(*args, **kwargs):
		if not is_vault_proxy_enabled():
			frappe.throw(_("Vault proxy is not enabled"), frappe.PermissionError)

		if not has_vault_access():
			frappe.throw(_("You do not have permission to access Vault"), frappe.PermissionError)

		return func(*args, **kwargs)

	return wrapper


def vault_secret_name_for_path(kv_path: str) -> str | None:
	"""Resolve an OpenBao KV path to the corresponding Vault Secret document name.

	Vault Secret documents use their path as the document name (autoname:
	field:path). For site-namespaced paths the relative portion after
	``frappe/{site}/`` is both the ``path`` field value and the document name.

	Args:
	    kv_path: OpenBao KV path, e.g. "frappe/{site}/myapp/api_key"

	Returns:
	    The Vault Secret document name, or None if no document tracks this path.
	"""
	prefix = f"frappe/{frappe.local.site}/"
	if not kv_path.startswith(prefix):
		return None
	relative = kv_path[len(prefix) :]
	# With autoname: "field:path", frappe.db.exists returns the name (== path)
	return frappe.db.exists("Vault Secret", relative)


def kv_path_from_api_path(api_path: str) -> str | None:
	"""Extract the KV path from a full OpenBao API path.

	/v1/secret/data/foo/bar     → foo/bar
	/v1/secret/metadata/foo/bar → foo/bar
	Any other path              → None
	"""
	for prefix in ("/v1/secret/data/", "/v1/secret/metadata/"):
		if api_path.startswith(prefix):
			return api_path[len(prefix) :]
	return None


def ensure_vault_secret(kv_path: str) -> None:
	"""Get or auto-create the Vault Secret doc for a proxy write path.

	If no document exists yet, creates the parent folder chain then the
	secret document itself. The caller is responsible for permission checking
	after this returns.

	Only acts on site-namespaced paths (``frappe/{site}/...``). Other paths
	are silently ignored.

	Args:
	    kv_path: OpenBao KV path, e.g. "frappe/{site}/myapp/api_key"
	"""
	if vault_secret_name_for_path(kv_path):
		return  # Document already exists

	prefix = f"frappe/{frappe.local.site}/"
	if not kv_path.startswith(prefix):
		return  # Not a site-namespaced path

	relative = kv_path[len(prefix) :]

	from frappe_vault.frappe_vault.doctype.vault_secret.vault_secret import ensure_folder_chain

	ensure_folder_chain(relative)

	parts = relative.split("/")
	parent_folder = "/".join(parts[:-1]) if len(parts) > 1 else None

	doc = frappe.new_doc("Vault Secret")
	doc.title = parts[-1]
	doc.path = relative
	doc.is_folder = 0
	doc.folder = parent_folder
	doc.insert()


def require_secret_permission(kv_path: str, ptype: str = "read") -> None:
	"""Assert the current user has ``ptype`` permission on the secret at kv_path.

	If the path maps to a Vault Secret document, delegates to
	``frappe.has_permission`` (which invokes the has_permission hook, folder
	ancestry walk, DocShare, and role checks). If no document exists for the
	path the function falls back to the coarse ``has_vault_access()`` role check.

	Args:
	    kv_path: OpenBao KV path, e.g. "frappe/{site}/myapp/api_key"
	    ptype: Frappe permission type — "read", "write", or "delete"

	Raises:
	    frappe.PermissionError
	"""
	secret_name = vault_secret_name_for_path(kv_path)
	if secret_name:
		if not frappe.has_permission("Vault Secret", ptype, doc=secret_name):
			frappe.throw(
				_("You do not have {0} permission on this secret").format(ptype),
				frappe.PermissionError,
			)
	elif not has_vault_access():
		frappe.throw(
			_("You do not have permission to access Vault"),
			frappe.PermissionError,
		)


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
		api_path = f"/v1/secret/metadata/{path}"
		response = client.make_request("LIST", api_path)

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
def get_secret_metadata(path: str) -> dict[str, Any]:
	"""
	Get metadata for a secret (without revealing the value).

	Requires read permission on the corresponding Vault Secret document.
	Falls back to role-based access for paths without a tracked document.

	Args:
	    path: Full KV path to the secret (e.g., "frappe/{site}/User/admin/password")

	Returns:
	    dict: Secret metadata including version info and timestamps
	"""
	if not is_vault_proxy_enabled():
		frappe.throw(_("Vault proxy is not enabled"), frappe.PermissionError)

	require_secret_permission(path, "read")

	try:
		client = get_vault_client()
		api_path = f"/v1/secret/metadata/{path}"
		response = client.make_request("GET", api_path)

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
def delete_secret(path: str) -> dict[str, Any]:
	"""
	Delete a secret.

	Requires delete permission on the corresponding Vault Secret document.
	Falls back to role-based access for paths without a tracked document.

	Args:
	    path: Full KV path to the secret (e.g., "frappe/{site}/User/admin/password")

	Returns:
	    dict: Success status
	"""
	if not is_vault_proxy_enabled():
		frappe.throw(_("Vault proxy is not enabled"), frappe.PermissionError)

	require_secret_permission(path, "delete")

	try:
		client = get_vault_client()
		api_path = f"/v1/secret/metadata/{path}"
		response = client.make_request("DELETE", api_path)

		if response.status_code not in (200, 204, 404):
			raise VaultError(f"Failed to delete secret: {response.status_code}")

		log_vault_access("delete_secret", api_path, True)
		return {"success": True}

	except VaultError as e:
		log_vault_access("delete_secret", f"/v1/secret/metadata/{path}", False, str(e))
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def proxy_request(path: str, method: str = "GET", data: str | None = None) -> dict[str, Any]:
	"""
	Generic proxy for OpenBao API requests.

	Permission checks are per-secret when the path maps to a Vault Secret
	document; otherwise falls back to role-based ``has_vault_access()``.

	For writes (POST/PUT) the corresponding Vault Secret document and its
	parent folder chain are auto-created if they do not yet exist.

	Args:
	    path: API path (e.g., "/v1/secret/data/frappe/{site}/myapp/config")
	    method: HTTP method (GET, POST, PUT, DELETE, LIST)
	    data: JSON string of request body for POST/PUT

	Returns:
	    dict: Response from OpenBao
	"""
	if not is_vault_proxy_enabled():
		frappe.throw(_("Vault proxy is not enabled"), frappe.PermissionError)

	if not path.startswith("/v1/"):
		return {"success": False, "error": "Invalid path - must start with /v1/"}

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

	kv_path = kv_path_from_api_path(path)

	# For writes, ensure the Vault Secret doc (and folder chain) exists first
	upper_method = method.upper()
	if upper_method in ("POST", "PUT") and kv_path:
		ensure_vault_secret(kv_path)

	# Determine the required permission type
	if upper_method == "DELETE":
		ptype = "delete"
	elif upper_method in ("POST", "PUT"):
		ptype = "write"
	else:
		ptype = "read"

	if kv_path:
		require_secret_permission(kv_path, ptype)
	elif not has_vault_access():
		frappe.throw(_("You do not have permission to access Vault"), frappe.PermissionError)

	try:
		client = get_vault_client()

		parsed_data = None
		if data:
			parsed_data = json.loads(data) if isinstance(data, str) else data

		response = client.make_request(method, path, data=parsed_data)

		log_vault_access("proxy_request", path, True, f"method={method}")

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
