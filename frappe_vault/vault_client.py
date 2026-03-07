# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import os
from typing import Any
from urllib.parse import quote

import frappe
import requests


class VaultError(Exception):
	"""Base exception for OpenBao errors."""

	pass


class VaultConnectionError(VaultError):
	"""Raised when OpenBao is unreachable."""

	pass


class VaultAuthError(VaultError):
	"""Raised when authentication fails (401/403)."""

	pass


class VaultClient:
	"""HTTP client for OpenBao KV v2 secrets engine.

	OpenBao is an open-source fork of HashiCorp Vault (MPL-2.0 licensed)
	that maintains API compatibility with Vault's KV v2 secrets engine.
	"""

	def __init__(
		self,
		url: str | None = None,
		token: str | None = None,
	):
		"""
		Initialize OpenBao client.

		Args:
		    url: OpenBao server URL (defaults to site_config or BAO_ADDR/VAULT_ADDR env var)
		    token: OpenBao token (defaults to site_config or BAO_TOKEN/VAULT_TOKEN env var)

		Note:
		    Environment variables are checked in order: BAO_* takes precedence over VAULT_*
		    for forward compatibility, but VAULT_* is still supported for backward compatibility.
		    OpenBao is expected to run on localhost; TLS is not used.
		"""
		# BAO_* env vars take precedence over VAULT_* for forward compatibility
		default_url = (
			os.environ.get("BAO_ADDR") or os.environ.get("VAULT_ADDR") or "http://localhost:8200"
		)
		default_token = os.environ.get("BAO_TOKEN") or os.environ.get("VAULT_TOKEN") or ""

		self.url = url or frappe.conf.get("vault_url") or default_url
		self.token = token or frappe.conf.get("vault_token") or default_token

		# Remove trailing slash from URL
		self.url = self.url.rstrip("/")

	def get_headers(self) -> dict[str, str]:
		"""Return headers for OpenBao API requests."""
		return {
			"X-Vault-Token": self.token,
			"Content-Type": "application/json",
		}

	def make_request(
		self,
		method: str,
		path: str,
		data: dict | None = None,
		timeout: int = 10,
	) -> requests.Response:
		"""
		Make an HTTP request to OpenBao.

		Args:
		    method: HTTP method (GET, POST, DELETE)
		    path: API path (will be appended to base URL)
		    data: JSON data for POST requests
		    timeout: Request timeout in seconds

		Returns:
		    Response object

		Raises:
		    VaultConnectionError: If OpenBao is unreachable
		    VaultAuthError: If authentication fails
		    VaultError: For other OpenBao errors
		"""
		url = f"{self.url}{path}"

		try:
			response = requests.request(
				method=method,
				url=url,
				headers=self.get_headers(),
				json=data,
				timeout=timeout,
			)
		except requests.exceptions.ConnectionError as e:
			raise VaultConnectionError(f"Cannot connect to OpenBao at {self.url}: {e}") from e
		except requests.exceptions.Timeout as e:
			raise VaultConnectionError(f"OpenBao request timed out: {e}") from e
		except requests.exceptions.RequestException as e:
			raise VaultError(f"OpenBao request failed: {e}") from e

		# Handle auth errors
		if response.status_code in (401, 403):
			raise VaultAuthError(f"OpenBao authentication failed: {response.text}")

		return response

	def get_secret_path(self, doctype: str, name: str, fieldname: str) -> str:
		"""
		Generate the OpenBao path for a secret.

		Secrets are namespaced by site to support multi-tenant deployments where
		multiple Frappe sites share the same OpenBao instance.

		Args:
		    doctype: Frappe DocType
		    name: Document name
		    fieldname: Field name

		Returns:
		    OpenBao KV v2 path (e.g., /v1/secret/data/frappe/mysite.example.com/User/admin/password)
		"""
		# Get current site for multi-tenancy support
		site = frappe.local.site

		# URL-encode components to handle special characters
		safe_site = quote(site, safe="")
		safe_doctype = quote(doctype, safe="")
		safe_name = quote(name, safe="")
		safe_fieldname = quote(fieldname, safe="")
		return f"/v1/secret/data/frappe/{safe_site}/{safe_doctype}/{safe_name}/{safe_fieldname}"

	def get_secret(self, doctype: str, name: str, fieldname: str) -> str | None:
		"""
		Retrieve a secret from OpenBao.

		Args:
		    doctype: Frappe DocType
		    name: Document name
		    fieldname: Field name

		Returns:
		    The secret value, or None if not found

		Raises:
		    VaultConnectionError: If OpenBao is unreachable
		    VaultAuthError: If authentication fails
		    VaultError: For other OpenBao errors
		"""
		path = self.get_secret_path(doctype, name, fieldname)
		response = self.make_request("GET", path)

		if response.status_code == 404:
			return None

		if response.status_code != 200:
			raise VaultError(f"Failed to get secret: {response.status_code} {response.text}")

		data = response.json()
		return data.get("data", {}).get("data", {}).get("value")

	def set_secret(self, doctype: str, name: str, fieldname: str, value: str) -> None:
		"""
		Store a secret in OpenBao.

		Args:
		    doctype: Frappe DocType
		    name: Document name
		    fieldname: Field name
		    value: The secret value to store

		Raises:
		    VaultConnectionError: If OpenBao is unreachable
		    VaultAuthError: If authentication fails
		    VaultError: For other OpenBao errors
		"""
		path = self.get_secret_path(doctype, name, fieldname)
		data = {"data": {"value": value}}
		response = self.make_request("POST", path, data=data)

		if response.status_code not in (200, 204):
			raise VaultError(f"Failed to set secret: {response.status_code} {response.text}")

	def delete_secret(self, doctype: str, name: str, fieldname: str) -> None:
		"""
		Delete a secret from OpenBao.

		Args:
		    doctype: Frappe DocType
		    name: Document name
		    fieldname: Field name

		Raises:
		    VaultConnectionError: If OpenBao is unreachable
		    VaultAuthError: If authentication fails
		    VaultError: For other OpenBao errors
		"""
		path = self.get_secret_path(doctype, name, fieldname)
		response = self.make_request("DELETE", path)

		# 404 is acceptable for delete (secret may not exist)
		if response.status_code not in (200, 204, 404):
			raise VaultError(f"Failed to delete secret: {response.status_code} {response.text}")

	def check_health(self) -> dict[str, Any]:
		"""
		Check OpenBao health status.

		Returns:
		    Health status dictionary

		Raises:
		    VaultConnectionError: If OpenBao is unreachable
		"""
		try:
			response = self.make_request("GET", "/v1/sys/health")
			return response.json()
		except VaultAuthError:
			# Health endpoint doesn't require auth, but may return different codes
			# based on seal status - still indicates OpenBao is reachable
			return {"reachable": True, "status": "unknown"}

	def is_available(self) -> bool:
		"""
		Check if OpenBao is available and responding.

		Returns:
		    True if OpenBao is available, False otherwise
		"""
		try:
			self.check_health()
			return True
		except VaultError:
			return False

	def get_secret_metadata(self, path: str) -> dict[str, Any] | None:
		"""
		Get KV v2 metadata for a secret path.

		Args:
		    path: Secret path relative to the KV mount (e.g., "frappe/User/admin/password")

		Returns:
		    Metadata dict with keys like 'created_time', 'updated_time', 'current_version',
		    'versions', etc. Returns None if the secret doesn't exist.

		Raises:
		    VaultConnectionError: If OpenBao is unreachable
		    VaultAuthError: If authentication fails
		    VaultError: For other OpenBao errors
		"""
		api_path = f"/v1/secret/metadata/{path}"
		response = self.make_request("GET", api_path)

		if response.status_code == 404:
			return None

		if response.status_code != 200:
			raise VaultError(f"Failed to get metadata: {response.status_code} {response.text}")

		return response.json().get("data", {})

	def get_secret_with_metadata(self, path: str) -> dict[str, Any] | None:
		"""
		Get a secret along with its KV v2 metadata.

		Args:
		    path: Secret path relative to the KV mount (e.g., "frappe/User/admin/password")

		Returns:
		    Dict with 'data' (the secret data) and 'metadata' (version info, timestamps).
		    Returns None if the secret doesn't exist.

		Raises:
		    VaultConnectionError: If OpenBao is unreachable
		    VaultAuthError: If authentication fails
		    VaultError: For other OpenBao errors
		"""
		api_path = f"/v1/secret/data/{path}"
		response = self.make_request("GET", api_path)

		if response.status_code == 404:
			return None

		if response.status_code != 200:
			raise VaultError(f"Failed to get secret: {response.status_code} {response.text}")

		return response.json().get("data", {})

	def set_secret_raw(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
		"""
		Store a secret at a raw path (not using doctype/name/field convention).

		Args:
		    path: Secret path relative to the KV mount (e.g., "frappe/User/admin/password")
		    data: The secret data to store (will be wrapped in {"data": ...})

		Returns:
		    Response data including version metadata

		Raises:
		    VaultConnectionError: If OpenBao is unreachable
		    VaultAuthError: If authentication fails
		    VaultError: For other OpenBao errors
		"""
		api_path = f"/v1/secret/data/{path}"
		response = self.make_request("POST", api_path, data={"data": data})

		if response.status_code not in (200, 204):
			raise VaultError(f"Failed to set secret: {response.status_code} {response.text}")

		if response.status_code == 204:
			return {}
		return response.json().get("data", {})

	def list_secrets(self, path: str = "frappe") -> list[str]:
		"""
		List secrets at a given path (non-recursive).

		Args:
		    path: Path to list (default: "frappe")

		Returns:
		    List of keys at the path. Keys ending with '/' are subdirectories.

		Raises:
		    VaultConnectionError: If OpenBao is unreachable
		    VaultAuthError: If authentication fails
		    VaultError: For other OpenBao errors
		"""
		api_path = f"/v1/secret/metadata/{path}"
		response = self.make_request("LIST", api_path)

		if response.status_code == 404:
			return []

		if response.status_code != 200:
			raise VaultError(f"Failed to list secrets: {response.status_code} {response.text}")

		return response.json().get("data", {}).get("keys", [])

	def list_secrets_recursive(self, path: str = "frappe") -> list[str]:
		"""
		Recursively list all secret paths under a given path.

		Args:
		    path: Root path to start listing from (default: "frappe")

		Returns:
		    List of full secret paths (excluding intermediate directories).
		    E.g., ["frappe/User/admin/password", "frappe/User/admin/api_key"]

		Raises:
		    VaultConnectionError: If OpenBao is unreachable
		    VaultAuthError: If authentication fails
		    VaultError: For other OpenBao errors
		"""
		result: list[str] = []
		keys = self.list_secrets(path)

		for key in keys:
			full_path = f"{path}/{key}".rstrip("/")
			if key.endswith("/"):
				# It's a directory, recurse into it
				result.extend(self.list_secrets_recursive(full_path))
			else:
				# It's a secret
				result.append(full_path)

		return result


# Per-site client cache. Keyed by site name so that workers serving multiple
# sites don't reuse a client initialised from a different site's config.
_clients: dict[str, VaultClient] = {}


def get_vault_client() -> VaultClient:
	"""
	Get the OpenBao client for the current Frappe site.

	Clients are cached per site so that multi-site workers each use their own
	configuration (vault_url / vault_token from their respective site_config.json).

	Returns:
	    VaultClient instance for the current site
	"""
	site = frappe.local.site
	if site not in _clients:
		_clients[site] = VaultClient()
	return _clients[site]


def reset_vault_client() -> None:
	"""Reset all cached OpenBao clients (useful for testing)."""
	_clients.clear()


def get_remote_clients() -> dict[str, VaultClient]:
	"""
	Get VaultClient instances for all configured remote OpenBao servers.

	Remote servers are configured in site_config.json under 'vault_remotes':

	    {
	        "vault_remotes": [
	            {"name": "site-b", "url": "https://site-b:8200", "token": "..."},
	            {"name": "site-n", "url": "https://site-n:8200", "token": "..."}
	        ]
	    }

	Returns:
	    Dict mapping remote name to VaultClient instance.
	    Empty dict if no remotes configured.
	"""
	remotes_config = frappe.conf.get("vault_remotes", [])
	clients: dict[str, VaultClient] = {}

	for remote in remotes_config:
		name = remote.get("name")
		url = remote.get("url")
		token = remote.get("token")

		if not all([name, url, token]):
			frappe.log_error(
				f"Invalid vault remote config: {remote}",
				"Vault Remote Config Error",
			)
			continue

		clients[name] = VaultClient(url=url, token=token)

	return clients


def is_sync_enabled() -> bool:
	"""
	Check if vault sync is enabled.

	Returns:
	    True if vault_sync_enabled is set in site_config and remotes are configured.
	"""
	return bool(frappe.conf.get("vault_sync_enabled", False) and frappe.conf.get("vault_remotes"))
