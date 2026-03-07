# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

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
		folder: DF.Link | None
		is_folder: DF.Check
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

	def on_update(self):
		"""Write secret value to OpenBao. Skipped for folder documents."""
		if self.is_folder:
			return

		if self.flags.get("secret_value") is not None:
			self.write_to_vault(self.flags.secret_value)
			return

		password_value = self.get_password("secret_value", raise_exception=False)
		if password_value:
			self.write_to_vault(password_value)

	def on_trash(self):
		"""Delete secret from OpenBao when document is deleted. Skipped for folders."""
		if self.is_folder:
			return

		try:
			client = get_vault_client()
			api_path = f"/v1/secret/metadata/frappe/{frappe.local.site}/{self.path}"
			client.make_request("DELETE", api_path)
			log_vault_access("delete_secret", self.path, True)
		except VaultError as e:
			log_vault_access("delete_secret", self.path, False, str(e))
			frappe.log_error(f"Failed to delete secret from OpenBao: {e}", "Vault Secret Deletion")

	def write_to_vault(self, value: str) -> None:
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
		if self.is_folder:
			return None
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
		"""Check if user has permission to access this secret via website/portal."""
		if not is_vault_secrets_api_enabled():
			return False

		if not user:
			user = frappe.session.user

		if user == "Guest":
			return False

		if doc.owner == user:
			return True

		return frappe.has_permission("Vault Secret", ptype, doc=doc, user=user)


def expand_folder_descendants(folder_names: list[str]) -> set[str]:
	"""Expand a set of folder names to include all their descendant folders.

	BFS through the Vault Secret tree collecting child folder names reachable
	from the given starting set. Used by get_permission_query_conditions to
	build the SQL IN clause for folder-level access.
	"""
	result = set(folder_names)
	frontier = list(folder_names)
	while frontier:
		children = frappe.get_all(
			"Vault Secret",
			filters={"folder": ["in", frontier], "is_folder": 1},
			pluck="name",
		)
		new = [c for c in children if c not in result]
		result.update(new)
		frontier = new
	return result


def ensure_folder_chain(relative_path: str) -> None:
	"""Ensure all parent folder documents exist for the given relative path.

	Creates missing Vault Secret folder docs for each path segment, working
	from root to leaf. Called before auto-creating a secret from a proxy write.

	Args:
	    relative_path: Path relative to frappe/{site}/, e.g. "customers/acme/api_key"
	"""
	parts = relative_path.split("/")
	# All segments except the last are folder segments
	for i in range(1, len(parts)):
		folder_path = "/".join(parts[:i])
		if frappe.db.exists("Vault Secret", folder_path):
			continue
		parent_path = "/".join(parts[: i - 1]) if i > 1 else None
		doc = frappe.new_doc("Vault Secret")
		doc.title = parts[i - 1]
		doc.path = folder_path
		doc.is_folder = 1
		doc.folder = parent_path
		doc.insert(ignore_permissions=True)


def has_permission(doc, ptype, user):
	"""Permission hook for Vault Secret.

	Returns False when the Vault Secrets API is disabled (blocks all users).
	Returns True when the user has a DocShare on any ancestor folder (folder
	inheritance). Returns None to fall through to Frappe's standard role and
	individual DocShare checks.

	Note: Frappe hard-bypasses this hook for Administrator — see
	get_permission_query_conditions and onload for full coverage.
	"""
	if not is_vault_secrets_api_enabled():
		return False

	# DocShare only exposes read/write/share columns; ancestry walk is not
	# meaningful for create/delete/etc. which are role-based only.
	if ptype not in ("read", "write", "share"):
		return None

	# Walk the folder ancestry: a DocShare on any ancestor folder grants access
	folder = doc.folder
	while folder:
		if frappe.db.get_value(
			"DocShare",
			{
				"share_doctype": "Vault Secret",
				"share_name": folder,
				"user": user,
				ptype: 1,
			},
			"name",
		):
			return True
		folder = frappe.db.get_value("Vault Secret", folder, "folder")

	return None  # fall through to Frappe role / individual DocShare checks


def get_permission_query_conditions(user: str) -> str:
	"""SQL condition applied to all Vault Secret list queries.

	Returns "1=0" when the API is disabled — this is NOT bypassed for
	Administrator, so it blocks the list view for everyone.

	When enabled, adds a condition to include secrets inside folders that have
	been DocShared with the user (folder ancestry expansion). Frappe's native
	role and DocShare handling covers the remaining cases.
	"""
	if not is_vault_secrets_api_enabled():
		return "1=0"

	# Find all Vault Secret folders that are directly DocShared with this user
	shared = frappe.get_all(
		"DocShare",
		filters={"share_doctype": "Vault Secret", "user": user, "read": 1},
		pluck="share_name",
	)
	if not shared:
		return ""

	# Filter to actual folder docs
	folder_docs = frappe.get_all(
		"Vault Secret",
		filters={"name": ["in", shared], "is_folder": 1},
		pluck="name",
	)
	if not folder_docs:
		return ""

	# Expand to all descendant folders so nested secrets are included
	all_folders = expand_folder_descendants(folder_docs)
	escaped = ", ".join(frappe.db.escape(f) for f in all_folders)
	return f"`tabVault Secret`.`folder` IN ({escaped})"
