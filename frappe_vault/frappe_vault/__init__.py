# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

"""
API endpoints for Vault Secrets.

These endpoints handle CRUD operations for Vault Secret documents
and integrate with OpenBao for secret value storage.
"""

from typing import Any

import frappe
from frappe import _
from frappe.desk.doctype.tag.tag import add_tag

from frappe_vault.frappe_vault.doctype.vault_secret.vault_secret import VaultSecret
from frappe_vault.vault_client import VaultError, get_vault_client
from frappe_vault.vault_proxy import is_vault_secrets_api_enabled, log_vault_access


def _check_secrets_api_enabled() -> None:
	"""Raise PermissionError if Vault Secrets API is not enabled in site config.

	Set ``vault_secrets_api_enabled: true`` in ``site_config.json`` to permit
	access to the Vault Secret doctype and its CRUD API, even for administrators.
	Without this key the vault operates as a local-only secret store.
	"""
	if not is_vault_secrets_api_enabled():
		frappe.throw(_("Vault Secrets API is not enabled"), frappe.PermissionError)


def _check_secret_permission(name: str, ptype: str = "read") -> None:
	"""Check if current user has permission on the secret.

	Args:
	    name: The Vault Secret document name
	    ptype: Permission type (read, write, share)

	Raises:
	    frappe.PermissionError: If user doesn't have permission
	"""
	if not frappe.has_permission("Vault Secret", ptype, doc=name):
		frappe.throw(_("You don't have permission to access this secret"), frappe.PermissionError)


def _get_user_secrets_filter() -> list:
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
	_check_secrets_api_enabled()
	filters = []

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

	# Enrich with tags from Frappe's built-in tagging system
	for secret in accessible_secrets:
		secret["tags"] = frappe.get_all(
			"Document User Tag",
			filters={"document_type": "Vault Secret", "document_name": secret["name"]},
			pluck="tag",
		)

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
	_check_secrets_api_enabled()
	_check_secret_permission(name, "read")

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
		"tags": frappe.get_all(
			"Document User Tag",
			filters={"document_type": "Vault Secret", "document_name": doc.name},
			pluck="tag",
		),
		"permissions": {
			"read": frappe.has_permission("Vault Secret", "read", doc=doc),
			"write": frappe.has_permission("Vault Secret", "write", doc=doc),
			"share": frappe.has_permission("Vault Secret", "share", doc=doc),
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
	_check_secrets_api_enabled()
	_check_secret_permission(name, "read")

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

	Args:
	    title: Human-readable name
	    path: OpenBao path
	    value: The secret value
	    description: Optional description
	    tags: Optional list of tag names (JSON string or list)

	Returns:
	    Created secret metadata
	"""
	_check_secrets_api_enabled()
	if isinstance(tags, str):
		import json

		tags = json.loads(tags) if tags else None

	doc = frappe.new_doc("Vault Secret")
	doc.title = title
	doc.path = path
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
	_check_secrets_api_enabled()
	_check_secret_permission(name, "write")

	if isinstance(tags, str):
		import json

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
	_check_secrets_api_enabled()
	_check_secret_permission(name, "delete")

	frappe.delete_doc("Vault Secret", name)


@frappe.whitelist()
def get_folders() -> list[str]:
	"""Get unique folder paths for tree navigation.

	Returns:
	    Sorted list of unique folder paths
	"""
	_check_secrets_api_enabled()
	filters = _get_user_secrets_filter()
	filters.append(["folder", "is", "set"])

	folders = frappe.get_all(
		"Vault Secret",
		filters=filters,
		pluck="folder",
		distinct=True,
	)

	# Build complete folder hierarchy
	all_folders = set()
	for folder in folders:
		if folder:
			parts = folder.split("/")
			for i in range(len(parts)):
				all_folders.add("/".join(parts[: i + 1]))

	return sorted(list(all_folders))


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
	_check_secrets_api_enabled()
	_check_secret_permission(name, "share")

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
	_check_secrets_api_enabled()
	_check_secret_permission(name, "read")

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
	_check_secrets_api_enabled()
	_check_secret_permission(name, "share")

	frappe.share.remove("Vault Secret", name, user)

	log_vault_access("remove_share", name, True, f"removed {user}")


def has_website_permission(doc, ptype, user=None, verbose=False):
	"""Website permission handler for Vault Secret.

	Registered in hooks.py to control portal access. Denies access unless
	vault_secrets_api_enabled is set in site_config.json.
	"""
	if not is_vault_secrets_api_enabled():
		return False
	return VaultSecret.has_website_permission(doc, ptype, user, verbose)
