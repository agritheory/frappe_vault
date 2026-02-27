# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_vault.vault_client import VaultError, get_vault_client
from frappe_vault.vault_proxy import is_vault_secrets_api_enabled, log_vault_access


class VaultSecret(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		description: DF.SmallText | None
		folder: DF.Data | None
		path: DF.Data
		secret_value: DF.Password | None
		title: DF.Data
	# end: auto-generated types

	def onload(self):
		"""Block form loading when the Vault Secrets API is disabled.

		Fires before the document is sent to the browser. Using onload instead
		of validate/before_save means read-only form access is also blocked,
		and it covers Administrator (who bypasses frappe.has_permission checks).
		"""
		if not is_vault_secrets_api_enabled():
			frappe.throw(_("Vault Secrets API is not enabled"), frappe.PermissionError)

	def before_save(self):
		"""Auto-derive folder from path."""
		if self.path:
			# Folder is everything before the last slash
			if "/" in self.path:
				self.folder = "/".join(self.path.split("/")[:-1])
			else:
				self.folder = ""

	def on_update(self):
		"""Write secret value to OpenBao."""
		if self.flags.get("secret_value") is not None:
			self._write_to_vault(self.flags.secret_value)
			return

		password_value = self.get_password("secret_value", raise_exception=False)
		if password_value:
			self._write_to_vault(password_value)

	def on_trash(self):
		"""Delete secret from OpenBao when document is deleted."""
		try:
			client = get_vault_client()
			# Delete using the metadata endpoint to remove all versions
			api_path = f"/v1/secret/metadata/frappe/{frappe.local.site}/{self.path}"
			client._make_request("DELETE", api_path)
			log_vault_access("delete_secret", self.path, True)
		except VaultError as e:
			log_vault_access("delete_secret", self.path, False, str(e))
			# Don't block deletion if vault is unavailable
			frappe.log_error(f"Failed to delete secret from OpenBao: {e}", "Vault Secret Deletion")

	def _write_to_vault(self, value: str) -> None:
		"""Write secret value to OpenBao."""
		try:
			client = get_vault_client()
			vault_path = f"frappe/{frappe.local.site}/{self.path}"
			client.set_secret_raw(vault_path, {"value": value})
			log_vault_access("write_secret", self.path, True)
		except VaultError as e:
			log_vault_access("write_secret", self.path, False, str(e))
			frappe.throw(_("Failed to write secret to OpenBao: {0}").format(str(e)))

	def get_secret_value(self) -> str | None:
		"""Retrieve secret value from OpenBao."""
		try:
			client = get_vault_client()
			vault_path = f"frappe/{frappe.local.site}/{self.path}"
			result = client.get_secret_with_metadata(vault_path)

			if not result:
				return None

			return result.get("data", {}).get("value")

		except VaultError as e:
			frappe.log_error(f"Failed to retrieve secret from OpenBao: {e}", "Vault Secret Retrieval")
			return None

	@staticmethod
	def has_website_permission(doc, ptype, user=None, verbose=False):
		"""Check if user has permission to access this secret via website/portal.

		This is called by Frappe's permission system for portal access.
		Access is granted if:
		1. vault_secrets_api_enabled is set in site_config.json
		2. User owns the document or has DocShare access
		"""
		if not is_vault_secrets_api_enabled():
			return False

		if not user:
			user = frappe.session.user

		if user == "Guest":
			return False

		# Check ownership
		if doc.owner == user:
			return True

		# Check DocShare
		return frappe.has_permission("Vault Secret", ptype, doc=doc, user=user)


def has_permission(doc, ptype, user):
	"""Block all Vault Secret access unless vault_secrets_api_enabled is set.

	Registered as a ``has_permission`` hook in hooks.py. Returns ``False`` to
	deny access for non-Administrator users when the Vault Secrets API is
	disabled. Returns ``None`` to fall through to normal role-based checks
	when it is enabled.

	Note: Frappe hard-bypasses this hook for Administrator — see
	``get_permission_query_conditions`` and ``onload`` for full coverage.
	"""
	if not is_vault_secrets_api_enabled():
		return False
	return None


def get_permission_query_conditions(user: str) -> str:
	"""Return SQL condition that blocks all rows when the API is disabled.

	Registered as a ``permission_query_conditions`` hook in hooks.py. Unlike
	``has_permission``, this hook is applied at the SQL level and is NOT
	bypassed for Administrator, so it blocks the list view for everyone.
	"""
	if not is_vault_secrets_api_enabled():
		return "1=0"
	return ""
