# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

"""
API endpoints for Vault Secrets.

These endpoints handle CRUD operations for Vault Secret documents
and integrate with OpenBao for secret value storage.
"""

import json
from typing import Any

import frappe
from frappe import _
from frappe.desk.doctype.tag.tag import add_tag

from frappe_vault.frappe_vault.doctype.vault_secret.vault_secret import (
	VaultSecret,
	ensure_folder_chain,
	has_permission as vault_has_permission,
)
from frappe_vault.vault_client import VaultError, get_vault_client
from frappe_vault.vault_proxy import is_vault_secrets_api_enabled, log_vault_access


def check_secrets_api_enabled() -> None:
	"""Raise PermissionError if Vault Secrets API is not enabled in site config.

	Set ``vault_secrets_api_enabled: true`` in ``site_config.json`` to permit
	access to the Vault Secret doctype and its CRUD API, even for administrators.
	Without this key the vault operates as a local-only secret store.
	"""
	if not is_vault_secrets_api_enabled():
		frappe.throw(_("Vault Secrets API is not enabled"), frappe.PermissionError)


def folder_ancestry_has_permission(doc, ptype: str, user: str) -> bool:
	"""Check folder ancestry for DocShare-based grants.

	Used as an explicit fallback because Frappe's hook dispatch may be skipped
	when the user has no applicable role for the doctype and the engine
	short-circuits before calling our vault_secret.has_permission hook.
	"""
	if ptype not in ("read", "write", "share"):
		return False
	return vault_has_permission(doc, ptype, user) is True


def effective_permission(doc, ptype: str) -> bool:
	"""Return True if the current user effectively has *ptype* on *doc*.

	Mirrors _check_secret_permission but returns a bool instead of raising,
	so it can be used to populate the permissions dict in get_secret().
	"""
	user = frappe.session.user
	if user == "Administrator":
		return True
	if frappe.has_permission("Vault Secret", ptype, doc=doc):
		return True
	return folder_ancestry_has_permission(doc, ptype, user)


def check_secret_permission(name: str, ptype: str = "read") -> None:
	"""Check if current user has permission on the secret.

	Calls frappe.has_permission (which invokes the vault_secret.has_permission
	hook and all standard role/DocShare checks). For read/write/share we also
	perform an explicit folder ancestry walk as a fallback: in Frappe v15 the
	custom hook dispatch may be skipped when the user has no applicable role
	permissions, causing the hook's folder-based grants to be silently ignored.

	Args:
	    name: The Vault Secret document name
	    ptype: Permission type (read, write, share, delete)

	Raises:
	    frappe.PermissionError: If user doesn't have permission
	"""
	if frappe.session.user == "Administrator":
		return

	if frappe.has_permission("Vault Secret", ptype, doc=name):
		return

	doc = frappe.get_doc("Vault Secret", name)
	if folder_ancestry_has_permission(doc, ptype, frappe.session.user):
		return

	frappe.throw(_("You don't have permission to access this secret"), frappe.PermissionError)


def get_doc_tags(doctype: str, docname: str) -> list[str]:
	"""Return the list of tags applied to a document.

	Uses raw SQL to avoid frappe.get_all's DocType meta validation step, which
	can raise DoesNotExistError in cold-cache test environments. Tries the
	Frappe v14/v15 table (tabDocument User Tag) first, then the v16+ table
	(tabTag Link) as a fallback.
	"""
	for table in ("tabDocument User Tag", "tabTag Link"):
		try:
			rows = frappe.db.sql(
				f"SELECT `tag` FROM `{table}`"  # noqa: S608
				" WHERE `document_type` = %s AND `document_name` = %s",
				(doctype, docname),
			)
			return [r[0] for r in rows]
		except Exception:
			continue
	return []


def get_user_secrets_filter() -> list:
	"""Build filter for secrets the user has access to.

	Uses Frappe's permission system with proper precedence:
	1. DocShare (most specific)
	2. User Permission
	3. Role Permission (least specific)

	Returns empty list to get all secrets, then filters by has_permission.
	"""
	# Return empty filter to get all secrets
	# We'll filter by permissions after fetching
	return []


@frappe.whitelist()
def get_secrets(
	folder: str | None = None,
	tag: str | None = None,
	search: str | None = None,
) -> list[dict[str, Any]]:
	"""List secrets the current user has access to.

	Uses Frappe's permission system with proper precedence:
	1. DocShare (most specific)
	2. User Permission
	3. Role Permission (least specific)

	Args:
	    folder: Filter by folder path
	    tag: Filter by tag name
	    search: Search in title, path, description

	Returns:
	    List of secret metadata dictionaries
	"""
	check_secrets_api_enabled()
	filters: list = [["is_folder", "=", 0]]

	if folder:
		filters.append(["folder", "=", folder])

	if search:
		filters.append(
			[
				"title",
				"like",
				f"%{search}%",
			]
		)

	secrets = frappe.get_all(
		"Vault Secret",
		filters=filters,
		fields=[
			"name",
			"title",
			"path",
			"folder",
			"description",
			"owner",
			"modified",
		],
		order_by="modified desc",
	)

	# Filter by permissions using has_permission
	# This checks DocShare > User Permission > Role Permission
	user = frappe.session.user
	accessible_secrets = []
	for secret in secrets:
		if frappe.has_permission("Vault Secret", "read", doc=secret["name"], user=user):
			accessible_secrets.append(secret)

	# Enrich with tags from Frappe's built-in tagging system.
	# Use raw SQL to avoid frappe.get_all's DocType meta validation, which can
	# raise DoesNotExistError in test environments where the meta cache is cold.
	for secret in accessible_secrets:
		secret["tags"] = get_doc_tags("Vault Secret", secret["name"])

	# Filter by tag if specified
	if tag:
		accessible_secrets = [s for s in accessible_secrets if tag in s.get("tags", [])]

	return accessible_secrets


@frappe.whitelist()
def get_secret(name: str) -> dict[str, Any]:
	"""Get secret metadata (not the value).

	Args:
	    name: The Vault Secret document name

	Returns:
	    Secret metadata dictionary with permissions info
	"""
	check_secrets_api_enabled()
	check_secret_permission(name, "read")

	doc = frappe.get_doc("Vault Secret", name)

	result = {
		"name": doc.name,
		"title": doc.title,
		"path": doc.path,
		"folder": doc.folder,
		"description": doc.description,
		"owner": doc.owner,
		"modified": doc.modified,
		"modified_by": doc.modified_by,
		"tags": get_doc_tags("Vault Secret", doc.name),
		"permissions": {
			"read": effective_permission(doc, "read"),
			"write": effective_permission(doc, "write"),
			"share": effective_permission(doc, "share"),
		},
	}

	return result


@frappe.whitelist()
def reveal_secret(name: str) -> dict[str, Any]:
	"""Fetch the actual secret value from OpenBao.

	Args:
	    name: The Vault Secret document name

	Returns:
	    Dictionary with the secret value
	"""
	check_secrets_api_enabled()
	check_secret_permission(name, "read")

	doc = frappe.get_doc("Vault Secret", name)
	value = doc.get_secret_value()

	log_vault_access("reveal_secret", doc.path, True)

	return {"value": value}


@frappe.whitelist()
def create_secret(
	title: str,
	path: str,
	value: str,
	description: str | None = None,
	tags: str | list | None = None,
) -> dict[str, Any]:
	"""Create a new secret.

	Ensures the parent folder chain exists before inserting. The folder field
	is set to the parent folder's document name (= parent folder path).

	Args:
	    title: Human-readable name
	    path: OpenBao path (e.g. "customers/acme/api_key")
	    value: The secret value
	    description: Optional description
	    tags: Optional list of tag names (JSON string or list)

	Returns:
	    Created secret metadata
	"""
	check_secrets_api_enabled()
	if isinstance(tags, str):
		tags = json.loads(tags) if tags else None

	ensure_folder_chain(path)

	parent_folder = "/".join(path.split("/")[:-1]) if "/" in path else None

	doc = frappe.new_doc("Vault Secret")
	doc.title = title
	doc.path = path
	doc.folder = parent_folder
	doc.description = description
	doc.flags.secret_value = value
	doc.insert()

	if tags:
		for tag_name in tags:
			add_tag(tag_name, "Vault Secret", doc.name)

	return get_secret(doc.name)


@frappe.whitelist()
def update_secret(
	name: str,
	title: str | None = None,
	description: str | None = None,
	value: str | None = None,
	tags: str | list | None = None,
) -> dict[str, Any]:
	"""Update a secret's metadata and/or value.

	Args:
	    name: The Vault Secret document name
	    title: New title (optional)
	    description: New description (optional)
	    value: New secret value (optional)
	    tags: New list of tag names (optional, replaces existing; JSON string or list)

	Returns:
	    Updated secret metadata
	"""
	check_secrets_api_enabled()
	check_secret_permission(name, "write")

	if isinstance(tags, str):
		tags = json.loads(tags) if tags else None

	doc = frappe.get_doc("Vault Secret", name)

	if title is not None:
		doc.title = title
	if description is not None:
		doc.description = description
	if value is not None:
		doc.flags.secret_value = value

	doc.save()

	if tags is not None:
		frappe.db.delete("Document User Tag", {"document_type": "Vault Secret", "document_name": name})
		for tag_name in tags:
			add_tag(tag_name, "Vault Secret", name)

	return get_secret(doc.name)


@frappe.whitelist()
def delete_secret(name: str) -> None:
	"""Delete a secret.

	Args:
	    name: The Vault Secret document name
	"""
	check_secrets_api_enabled()
	check_secret_permission(name, "delete")

	frappe.delete_doc("Vault Secret", name)


@frappe.whitelist()
def get_folders() -> list[str]:
	"""Get folder paths the current user has read access to, for tree navigation.

	Folders are Vault Secret documents with is_folder=1. Access is filtered
	through the same has_permission hook used by get_secrets(), so folder
	inheritance via DocShare is respected.

	Returns:
	    Sorted list of accessible folder path strings
	"""
	check_secrets_api_enabled()
	user = frappe.session.user

	folders = frappe.get_all(
		"Vault Secret",
		filters={"is_folder": 1},
		fields=["name", "folder"],
		order_by="name asc",
	)

	return sorted(
		f["name"]
		for f in folders
		if frappe.has_permission("Vault Secret", "read", doc=f["name"], user=user)
	)


@frappe.whitelist()
def share_secret(
	name: str,
	user: str,
	read: int = 1,
	write: int = 0,
	share: int = 0,
) -> None:
	"""Share a secret with a user via DocShare.

	Args:
	    name: The Vault Secret document name
	    user: Email of user to share with
	    read: Grant read permission (default: 1)
	    write: Grant write permission (default: 0)
	    share: Grant share permission (default: 0)
	"""
	check_secrets_api_enabled()
	check_secret_permission(name, "share")

	frappe.share.add(
		"Vault Secret",
		name,
		user,
		read=int(read),
		write=int(write),
		share=int(share),
	)

	log_vault_access("share_secret", name, True, f"shared with {user}")


@frappe.whitelist()
def get_shared_users(name: str) -> list[dict[str, Any]]:
	"""Get list of users a secret is shared with.

	Args:
	    name: The Vault Secret document name

	Returns:
	    List of share records with user and permissions
	"""
	check_secrets_api_enabled()
	check_secret_permission(name, "read")

	shares = frappe.get_all(
		"DocShare",
		filters={
			"share_doctype": "Vault Secret",
			"share_name": name,
		},
		fields=["user", "read", "write", "share"],
	)

	return shares


@frappe.whitelist()
def remove_share(name: str, user: str) -> None:
	"""Remove a user's share access to a secret.

	Args:
	    name: The Vault Secret document name
	    user: Email of user to remove
	"""
	check_secrets_api_enabled()
	check_secret_permission(name, "share")

	frappe.share.remove("Vault Secret", name, user)

	log_vault_access("remove_share", name, True, f"removed {user}")


@frappe.whitelist()
def share_folder(
	name: str,
	user: str,
	read: int = 1,
	write: int = 0,
	share: int = 0,
) -> None:
	"""Share a folder with a user via DocShare.

	The folder must be a Vault Secret with is_folder=1. The share grants access
	to all secrets within the folder at any depth — the has_permission hook on
	Vault Secret walks the ancestry chain dynamically, so secrets added to the
	folder after sharing are covered automatically.

	Args:
	    name: The Vault Secret folder document name (= folder path)
	    user: Email of user to share with
	    read: Grant read permission (default: 1)
	    write: Grant write permission (default: 0)
	    share: Grant share permission (default: 0)
	"""
	check_secrets_api_enabled()
	check_secret_permission(name, "share")

	if not frappe.db.get_value("Vault Secret", name, "is_folder"):
		frappe.throw(_("'{0}' is not a folder").format(name), frappe.ValidationError)

	frappe.share.add(
		"Vault Secret",
		name,
		user,
		read=int(read),
		write=int(write),
		share=int(share),
	)

	log_vault_access("share_folder", name, True, f"shared with {user}")


def has_website_permission(doc, ptype, user=None, verbose=False):
	"""Website permission handler for Vault Secret.

	Registered in hooks.py to control portal access. Denies access unless
	vault_secrets_api_enabled is set in site_config.json.
	"""
	if not is_vault_secrets_api_enabled():
		return False
	return VaultSecret.has_website_permission(doc, ptype, user, verbose)
