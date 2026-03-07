# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

"""
Tests for the Vault Secret permission model.

Covers:
  - has_permission hook: folder ancestry walk via DocShare
  - get_permission_query_conditions: folder tree expansion for SQL
  - ensure_folder_chain: auto-creation of parent folder docs
  - get_folders: permission-filtered folder listing
  - share_folder: folder validation and DocShare creation

These tests mock the Frappe DB layer so they run without a live OpenBao
instance or full migration.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import frappe
import pytest

from frappe_vault.frappe_vault import get_folders, share_folder
from frappe_vault.frappe_vault.doctype.vault_secret.vault_secret import (
	ensure_folder_chain,
	expand_folder_descendants,
	get_permission_query_conditions,
	has_permission,
)

VAULT_ADMIN = "vault-admin@vault.test"
NO_ACCESS = "no-access@vault.test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_doc(folder=None, is_folder=0, name="some/secret"):
	"""Build a minimal Vault Secret-like namespace for has_permission calls."""
	return SimpleNamespace(folder=folder, is_folder=is_folder, name=name)


def secrets_api_enabled(monkeypatch):
	monkeypatch.setattr(
		"frappe_vault.frappe_vault.doctype.vault_secret.vault_secret.is_vault_secrets_api_enabled",
		lambda: True,
	)


def secrets_api_disabled(monkeypatch):
	monkeypatch.setattr(
		"frappe_vault.frappe_vault.doctype.vault_secret.vault_secret.is_vault_secrets_api_enabled",
		lambda: False,
	)


# ---------------------------------------------------------------------------
# has_permission — Vault Secrets API disabled
# ---------------------------------------------------------------------------


def test_has_permission_blocked_when_api_disabled(monkeypatch):
	"""has_permission returns False for any user when the API is disabled."""
	secrets_api_disabled(monkeypatch)
	doc = make_doc()
	assert has_permission(doc, "read", VAULT_ADMIN) is False


# ---------------------------------------------------------------------------
# has_permission — no folder ancestry
# ---------------------------------------------------------------------------


def test_has_permission_no_folder_falls_through(monkeypatch):
	"""A secret with no folder returns None (fall through to Frappe role checks)."""
	secrets_api_enabled(monkeypatch)
	doc = make_doc(folder=None)
	with patch.object(frappe.db, "get_value", return_value=None):
		result = has_permission(doc, "read", NO_ACCESS)
	assert result is None


# ---------------------------------------------------------------------------
# has_permission — single-level folder share
# ---------------------------------------------------------------------------


def test_folder_share_grants_access_to_child_secret(monkeypatch):
	"""DocShare on the direct parent folder grants read on the child secret."""
	secrets_api_enabled(monkeypatch)
	doc = make_doc(folder="deploy", name="deploy/api_key")

	# First call: look for DocShare on "deploy" → found (returns a truthy name)
	# Second call would be to get the parent folder of "deploy", but we stop early
	with patch.object(frappe.db, "get_value", return_value="DocShare-001"):
		result = has_permission(doc, "read", NO_ACCESS)

	assert result is True


def test_folder_share_write_grants_write(monkeypatch):
	"""DocShare with write=1 on a folder grants write on child secrets."""
	secrets_api_enabled(monkeypatch)
	doc = make_doc(folder="deploy", name="deploy/db_password")

	with patch.object(frappe.db, "get_value", return_value="DocShare-002"):
		result = has_permission(doc, "write", NO_ACCESS)

	assert result is True


# ---------------------------------------------------------------------------
# has_permission — multi-level ancestry
# ---------------------------------------------------------------------------


def test_folder_share_grants_access_at_depth(monkeypatch):
	"""DocShare on a grandparent folder grants access to a deeply nested secret."""
	secrets_api_enabled(monkeypatch)
	# Secret is at customers/acme/deploy/api_key; folder = customers/acme/deploy
	doc = make_doc(folder="customers/acme/deploy", name="customers/acme/deploy/api_key")

	# Simulate: no share on "customers/acme/deploy", no share on "customers/acme",
	# but share found on "customers"
	call_log = []

	def fake_get_value(doctype, filters_or_name, fieldname):
		if doctype == "DocShare":
			call_log.append(filters_or_name["share_name"])
			# Grant access only for the "customers" folder
			return "DocShare-003" if filters_or_name["share_name"] == "customers" else None
		if doctype == "Vault Secret":
			# Walk: deploy → acme → customers → None
			parents = {
				"customers/acme/deploy": "customers/acme",
				"customers/acme": "customers",
				"customers": None,
			}
			return parents.get(filters_or_name)
		return None

	with patch.object(frappe.db, "get_value", side_effect=fake_get_value):
		result = has_permission(doc, "read", NO_ACCESS)

	assert result is True
	assert call_log == ["customers/acme/deploy", "customers/acme", "customers"]


# ---------------------------------------------------------------------------
# has_permission — revocation (no share anywhere in ancestry)
# ---------------------------------------------------------------------------


def test_folder_share_revocation_denies_access(monkeypatch):
	"""If no ancestor folder has a DocShare for the user, return None (not True)."""
	secrets_api_enabled(monkeypatch)
	doc = make_doc(folder="customers/acme/deploy", name="customers/acme/deploy/api_key")

	def fake_get_value(doctype, filters_or_name, fieldname):
		if doctype == "DocShare":
			return None  # No share anywhere
		parents = {
			"customers/acme/deploy": "customers/acme",
			"customers/acme": "customers",
			"customers": None,
		}
		return parents.get(filters_or_name)

	with patch.object(frappe.db, "get_value", side_effect=fake_get_value):
		result = has_permission(doc, "read", NO_ACCESS)

	assert result is None


# ---------------------------------------------------------------------------
# _expand_folder_descendants
# ---------------------------------------------------------------------------


def test_expand_folder_descendants_single_level(monkeypatch):
	"""Expanding a folder with one level of children returns all those children."""
	# "deploy" has children "deploy/a" and "deploy/b"; no further children
	def fake_get_all(doctype, filters=None, pluck=None, **kw):
		if filters and filters.get("folder") == ["in", ["deploy"]]:
			return ["deploy/a", "deploy/b"]
		return []

	with patch.object(frappe, "get_all", side_effect=fake_get_all):
		result = expand_folder_descendants(["deploy"])

	assert "deploy" in result
	assert "deploy/a" in result
	assert "deploy/b" in result


def test_expand_folder_descendants_deep(monkeypatch):
	"""BFS expansion reaches folders at multiple depths."""
	tree = {
		"root": ["root/a", "root/b"],
		"root/a": ["root/a/x"],
		"root/b": [],
		"root/a/x": [],
	}

	def fake_get_all(doctype, filters=None, pluck=None, **kw):
		frontier = filters.get("folder", {})
		if isinstance(frontier, list) and frontier[0] == "in":
			keys = frontier[1]
		else:
			return []
		result = []
		for k in keys:
			result.extend(tree.get(k, []))
		return result

	with patch.object(frappe, "get_all", side_effect=fake_get_all):
		result = expand_folder_descendants(["root"])

	assert result == {"root", "root/a", "root/b", "root/a/x"}


# ---------------------------------------------------------------------------
# get_permission_query_conditions
# ---------------------------------------------------------------------------


def test_permission_query_conditions_disabled(monkeypatch):
	"""API disabled → always return '1=0'."""
	secrets_api_disabled(monkeypatch)
	assert get_permission_query_conditions(NO_ACCESS) == "1=0"


def test_permission_query_conditions_no_shares(monkeypatch):
	"""No DocShares for user → empty string (no extra restriction)."""
	secrets_api_enabled(monkeypatch)
	with patch.object(frappe, "get_all", return_value=[]):
		result = get_permission_query_conditions(NO_ACCESS)
	assert result == ""


def test_permission_query_conditions_folder_expansion(monkeypatch):
	"""Folders shared with user are expanded and emitted as a SQL IN clause."""
	secrets_api_enabled(monkeypatch)

	def fake_get_all(doctype, filters=None, pluck=None, **kw):
		if doctype == "DocShare":
			return ["deploy"]
		if doctype == "Vault Secret" and filters.get("name") == ["in", ["deploy"]]:
			return ["deploy"]  # "deploy" is a real folder
		# BFS: deploy has no sub-folders
		if doctype == "Vault Secret" and filters.get("folder") == ["in", ["deploy"]]:
			return []
		return []

	with patch.object(frappe, "get_all", side_effect=fake_get_all):
		with patch.object(frappe.db, "escape", side_effect=lambda s: f"'{s}'"):
			result = get_permission_query_conditions(NO_ACCESS)

	assert "`tabVault Secret`.`folder` IN" in result
	assert "'deploy'" in result


# ---------------------------------------------------------------------------
# _ensure_folder_chain
# ---------------------------------------------------------------------------


def test_ensure_folder_chain_creates_missing_folders(monkeypatch):
	"""Missing parent folders are created bottom-up from root to leaf."""
	created_docs = []

	def fake_exists(doctype, name):
		return None  # Nothing exists yet

	def fake_new_doc(doctype):
		doc = MagicMock()
		doc.name = None

		def capture_insert(ignore_permissions=False):
			created_docs.append(
				{
					"title": doc.title,
					"path": doc.path,
					"is_folder": doc.is_folder,
					"folder": doc.folder,
				}
			)

		doc.insert = capture_insert
		return doc

	with patch.object(frappe.db, "exists", side_effect=fake_exists), patch.object(
		frappe, "new_doc", side_effect=fake_new_doc
	):
		ensure_folder_chain("customers/acme/deploy/api_key")

	# Should create 3 folders: customers, customers/acme, customers/acme/deploy
	assert len(created_docs) == 3
	paths = [d["path"] for d in created_docs]
	assert paths == ["customers", "customers/acme", "customers/acme/deploy"]

	assert created_docs[0] == {
		"title": "customers",
		"path": "customers",
		"is_folder": 1,
		"folder": None,
	}
	assert created_docs[1] == {
		"title": "acme",
		"path": "customers/acme",
		"is_folder": 1,
		"folder": "customers",
	}
	assert created_docs[2] == {
		"title": "deploy",
		"path": "customers/acme/deploy",
		"is_folder": 1,
		"folder": "customers/acme",
	}


def test_ensure_folder_chain_skips_existing(monkeypatch):
	"""Folders that already exist are not re-created."""
	created_docs = []

	def fake_exists(doctype, name):
		# "customers" already exists; "customers/acme" does not
		return name if name == "customers" else None

	def fake_new_doc(doctype):
		doc = MagicMock()

		def capture_insert(ignore_permissions=False):
			created_docs.append(doc.path)

		doc.insert = capture_insert
		return doc

	with patch.object(frappe.db, "exists", side_effect=fake_exists), patch.object(
		frappe, "new_doc", side_effect=fake_new_doc
	):
		ensure_folder_chain("customers/acme/key")

	# Only "customers/acme" should be created; "customers" existed
	assert created_docs == ["customers/acme"]


def test_ensure_folder_chain_flat_path_no_folders(monkeypatch):
	"""A single-segment path (no parent) creates no folder docs."""
	with patch.object(frappe, "new_doc") as mock_new_doc:
		ensure_folder_chain("top_level_secret")
	mock_new_doc.assert_not_called()


# ---------------------------------------------------------------------------
# get_folders — permission-filtered listing
# ---------------------------------------------------------------------------


def test_get_folders_filters_by_permission(monkeypatch):
	"""get_folders returns only folders the current user can read."""
	monkeypatch.setattr(
		"frappe_vault.frappe_vault.is_vault_secrets_api_enabled",
		lambda: True,
	)
	frappe.set_user(NO_ACCESS)

	all_folders = [
		{"name": "deploy", "folder": None},
		{"name": "customers/acme", "folder": "customers"},
		{"name": "internal", "folder": None},
	]

	def fake_has_permission(doctype, ptype, doc, user=None):
		# Only "deploy" is accessible to NO_ACCESS
		return doc == "deploy"

	with patch.object(frappe, "get_all", return_value=all_folders), patch.object(
		frappe, "has_permission", side_effect=fake_has_permission
	):
		result = get_folders()

	assert result == ["deploy"]
	assert "customers/acme" not in result
	assert "internal" not in result


def test_get_folders_blocked_when_api_disabled(monkeypatch):
	"""get_folders raises PermissionError when the Vault Secrets API is disabled."""
	# Patch at the import site in frappe_vault.frappe_vault so the _check call sees False
	monkeypatch.setattr(
		"frappe_vault.frappe_vault.is_vault_secrets_api_enabled",
		lambda: False,
	)
	# Also patch the conf key so that the real is_vault_secrets_api_enabled()
	# returns False even if site_config.json has it set to true.
	monkeypatch.setitem(frappe.conf, "vault_secrets_api_enabled", False)
	frappe.set_user(VAULT_ADMIN)

	with pytest.raises(frappe.PermissionError):
		get_folders()


# ---------------------------------------------------------------------------
# share_folder
# ---------------------------------------------------------------------------


def test_share_folder_validates_is_folder(monkeypatch):
	"""share_folder raises ValidationError when the target doc is not a folder."""
	monkeypatch.setattr(
		"frappe_vault.frappe_vault.is_vault_secrets_api_enabled",
		lambda: True,
	)
	frappe.set_user(VAULT_ADMIN)

	with patch("frappe_vault.frappe_vault.check_secret_permission"), patch.object(
		frappe.db, "get_value", return_value=0  # is_folder = 0
	):
		with pytest.raises(frappe.ValidationError):
			share_folder("deploy/api_key", NO_ACCESS)


def test_share_folder_calls_frappe_share_add(monkeypatch):
	"""share_folder delegates to frappe.share.add for valid folder docs."""
	monkeypatch.setattr(
		"frappe_vault.frappe_vault.is_vault_secrets_api_enabled",
		lambda: True,
	)
	frappe.set_user(VAULT_ADMIN)

	# frappe.share is a submodule; patch its .add function directly so the test
	# doesn't depend on whether the submodule is already imported as an attribute.
	with patch("frappe_vault.frappe_vault.check_secret_permission"), patch.object(
		frappe.db, "get_value", return_value=1  # is_folder = 1
	), patch("frappe.share.add") as mock_add, patch("frappe_vault.frappe_vault.log_vault_access"):
		share_folder("deploy", NO_ACCESS, read=1, write=0, share=0)

	mock_add.assert_called_once_with("Vault Secret", "deploy", NO_ACCESS, read=1, write=0, share=0)
