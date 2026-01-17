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

	def _get_headers(self) -> dict[str, str]:
		"""Return headers for OpenBao API requests."""
		return {
			"X-Vault-Token": self.token,
			"Content-Type": "application/json",
		}

	def _make_request(
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
				headers=self._get_headers(),
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

	def _get_secret_path(self, doctype: str, name: str, fieldname: str) -> str:
		"""
		Generate the OpenBao path for a secret.

		Args:
		    doctype: Frappe DocType
		    name: Document name
		    fieldname: Field name

		Returns:
		    OpenBao KV v2 path (e.g., /v1/secret/data/frappe/User/admin/password)
		"""
		# URL-encode components to handle special characters
		safe_doctype = quote(doctype, safe="")
		safe_name = quote(name, safe="")
		safe_fieldname = quote(fieldname, safe="")
		return f"/v1/secret/data/frappe/{safe_doctype}/{safe_name}/{safe_fieldname}"

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
		path = self._get_secret_path(doctype, name, fieldname)
		response = self._make_request("GET", path)

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
		path = self._get_secret_path(doctype, name, fieldname)
		data = {"data": {"value": value}}
		response = self._make_request("POST", path, data=data)

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
		path = self._get_secret_path(doctype, name, fieldname)
		response = self._make_request("DELETE", path)

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
			response = self._make_request("GET", "/v1/sys/health")
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


# Module-level client instance (lazy initialization)
_client: VaultClient | None = None


def get_vault_client() -> VaultClient:
	"""
	Get the module-level OpenBao client instance.

	Returns:
	    VaultClient instance
	"""
	global _client
	if _client is None:
		_client = VaultClient()
	return _client


def reset_vault_client() -> None:
	"""Reset the module-level OpenBao client (useful for testing)."""
	global _client
	_client = None
