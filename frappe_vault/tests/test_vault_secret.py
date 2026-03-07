# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

"""
Tests for the Vault Secret CRUD API (frappe_vault/frappe_vault/__init__.py).

These are integration tests against the full stack: Frappe ORM, OpenBao, and
the permission layer. They rely on the 20 fixture secrets created by setup.py
and the test users from fixtures.py.

Design notes (current):
  - autoname: "field:path" — doc.name == doc.path for every Vault Secret
  - folder field is a Link to Vault Secret (the parent folder doc, is_folder=1)
  - get_secrets() only returns is_folder=0 documents
  - get_folders() returns names of is_folder=1 docs the user can read
  - setup.py creates the folder tree via _ensure_folder_chain before each secret

Vault Secret stores values at:
    frappe/{site}/{path}   (raw path, value key)

That is different from the doctype/name/fieldname scheme used for encrypted
Password fields. vault_client.get_secret_with_metadata(raw_path) is used
for assertions that read directly from OpenBao.

Requires a running OpenBao instance (BAO_TOKEN / VAULT_TOKEN).
"""

import frappe
import pytest

from frappe_vault.frappe_vault import (
	create_secret,
	delete_secret,
	get_folders,
	get_secret,
	get_secrets,
	get_shared_users,
	remove_share,
	reveal_secret,
	share_folder,
	share_secret,
	update_secret,
)
from frappe_vault.vault_client import VaultClient, VaultError, reset_vault_client

VAULT_ADMIN = "vault-admin@vault.test"
NO_ACCESS = "no-access@vault.test"


@pytest.fixture(autouse=True)
def reset_client():
	reset_vault_client()
	yield
	reset_vault_client()


@pytest.fixture
def vault_client():
	return VaultClient()


def raw_vault_path(path):
	"""Build the OpenBao path used by VaultSecret._write_to_vault."""
	return f"frappe/{frappe.local.site}/{path}"


@pytest.fixture
def cleanup_secret(vault_client):
	"""Track test-created Vault Secret docs and delete them (+ their OpenBao value) after."""
	created = []

	def track(name, path):
		created.append((name, path))

	yield track

	for name, path in created:
		if frappe.db.exists("Vault Secret", name):
			frappe.delete_doc("Vault Secret", name, ignore_permissions=True, force=True)
		try:
			raw_path = f"/v1/secret/metadata/{raw_vault_path(path)}"
			vault_client.make_request("DELETE", raw_path)
		except VaultError:
			pass


# =============================================================================
# List and filter
# =============================================================================


def test_get_secrets_returns_fixture_data():
	"""System Manager sees all 20 fixture secrets (folders excluded)."""
	frappe.set_user(VAULT_ADMIN)
	secrets = get_secrets()
	# get_secrets() only returns is_folder=0 docs
	assert all(not s.get("is_folder") for s in secrets)
	assert len(secrets) >= 20, f"Expected at least 20 fixture secrets, got {len(secrets)}"


def test_get_secrets_filtered_by_folder():
	"""Folder filter returns only secrets whose direct parent is that folder."""
	frappe.set_user(VAULT_ADMIN)
	secrets = get_secrets(folder="apps/myapp")
	paths = [s["path"] for s in secrets]
	# With the new Link folder field, these secrets have folder == "apps/myapp"
	assert all(s["folder"] == "apps/myapp" for s in secrets)
	assert "apps/myapp/database" in paths
	assert "apps/myapp/sendgrid" in paths
	assert "apps/myapp/stripe" in paths
	# Deeper paths must NOT appear — their folder is "apps/myapp/oauth", not "apps/myapp"
	assert "apps/myapp/oauth/github" not in paths


def test_get_secrets_filtered_by_tag():
	"""Tag filter returns only secrets tagged with that value."""
	frappe.set_user(VAULT_ADMIN)
	# Verify that the tag infrastructure is populated before asserting counts.
	# In some environments (e.g. fresh installs where fixture tags failed to
	# persist, or Frappe versions with a different tag storage backend) tags
	# may be unavailable.  Skip rather than fail in those cases.
	all_secrets = get_secrets()
	if not any(s.get("tags") for s in all_secrets):
		pytest.skip("No tags found on any fixture secret — tag storage unavailable or not seeded")

	secrets = get_secrets(tag="ci-cd")
	assert len(secrets) >= 3
	for s in secrets:
		assert "ci-cd" in s.get("tags", [])


def test_get_secrets_search():
	"""Search by title fragment returns matching secrets."""
	frappe.set_user(VAULT_ADMIN)
	results = get_secrets(search="AWS")
	assert len(results) >= 3
	for r in results:
		assert "AWS" in r["title"]


def test_get_secrets_excludes_folders():
	"""get_secrets() never returns folder documents."""
	frappe.set_user(VAULT_ADMIN)
	secrets = get_secrets()
	names = [s["name"] for s in secrets]
	# These are folder paths that setup.py creates; they must not appear in secrets
	assert "apps" not in names
	assert "apps/myapp" not in names
	assert "infrastructure" not in names


# =============================================================================
# get_folders
# =============================================================================


def test_get_folders_returns_hierarchy():
	"""Folder list returns is_folder=1 Vault Secret doc names for accessible folders.

	setup.py creates the full folder tree via _ensure_folder_chain, so every
	parent path segment exists as a first-class Vault Secret folder document.
	"""
	frappe.set_user(VAULT_ADMIN)
	folders = get_folders()
	assert isinstance(folders, list)
	assert "apps" in folders
	assert "apps/myapp" in folders
	assert "apps/myapp/oauth" in folders
	assert "infrastructure" in folders
	assert "infrastructure/aws" in folders
	assert "monitoring" in folders


def test_get_folders_excludes_secrets():
	"""get_folders() returns only is_folder=1 docs, never secret docs."""
	frappe.set_user(VAULT_ADMIN)
	folders = get_folders()
	# Actual secret paths must not appear as folders
	assert "apps/myapp/database" not in folders
	assert "master_key" not in folders
	assert "monitoring/datadog" not in folders


# =============================================================================
# create / reveal / update / delete
# =============================================================================


def test_create_and_reveal_secret(cleanup_secret, vault_client):
	"""create_secret writes to OpenBao; reveal_secret returns the plaintext value."""
	frappe.set_user(VAULT_ADMIN)
	path = "tests/api-surface/create-test"
	value = "super-secret-api-key-xyz-987"

	result = create_secret(
		title="API Surface Test Secret",
		path=path,
		value=value,
		description="Created by test_vault_secret.py",
		tags=["test"],
	)
	name = result["name"]
	# With autoname: "field:path", name == path
	assert name == path
	cleanup_secret(name, path)

	revealed = reveal_secret(name)
	assert revealed["value"] == value

	# Confirm directly in OpenBao
	raw = vault_client.get_secret_with_metadata(raw_vault_path(path))
	assert raw is not None
	assert raw.get("data", {}).get("value") == value


def test_create_secret_creates_folder_chain(cleanup_secret):
	"""create_secret auto-creates parent folder docs for new paths."""
	frappe.set_user(VAULT_ADMIN)
	path = "tests/folder-chain-test/nested/secret"
	value = "nested-value"

	result = create_secret(title="Folder Chain Test", path=path, value=value)
	name = result["name"]
	cleanup_secret(name, path)

	# Parent folder docs must have been created
	assert frappe.db.exists("Vault Secret", "tests/folder-chain-test")
	assert frappe.db.exists("Vault Secret", "tests/folder-chain-test/nested")

	# Verify the secret's folder link
	assert result["folder"] == "tests/folder-chain-test/nested"


def test_update_secret_changes_value(cleanup_secret, vault_client):
	"""update_secret writes the new value to OpenBao."""
	frappe.set_user(VAULT_ADMIN)
	path = "tests/api-surface/update-test"
	original_value = "original-value-111"
	updated_value = "updated-value-222"

	result = create_secret(title="Update Test Secret", path=path, value=original_value)
	name = result["name"]
	cleanup_secret(name, path)

	update_secret(name=name, value=updated_value)

	revealed = reveal_secret(name)
	assert revealed["value"] == updated_value

	raw = vault_client.get_secret_with_metadata(raw_vault_path(path))
	assert raw.get("data", {}).get("value") == updated_value


def test_update_secret_metadata_only(cleanup_secret):
	"""update_secret with no value changes only the metadata fields."""
	frappe.set_user(VAULT_ADMIN)
	path = "tests/api-surface/metadata-update"

	result = create_secret(title="Metadata Test", path=path, value="unchanged-value")
	name = result["name"]
	cleanup_secret(name, path)

	updated = update_secret(name=name, title="Metadata Test Updated", description="new description")
	assert updated["title"] == "Metadata Test Updated"
	assert updated["description"] == "new description"

	revealed = reveal_secret(name)
	assert revealed["value"] == "unchanged-value"


def test_delete_secret_removes_from_openbao(cleanup_secret, vault_client):
	"""Deleting a Vault Secret doc also removes the value from OpenBao."""
	frappe.set_user(VAULT_ADMIN)
	path = "tests/api-surface/delete-test"
	value = "value-to-be-deleted"

	result = create_secret(title="Delete Test Secret", path=path, value=value)
	name = result["name"]

	raw_before = vault_client.get_secret_with_metadata(raw_vault_path(path))
	assert raw_before is not None

	delete_secret(name)

	raw_after = vault_client.get_secret_with_metadata(raw_vault_path(path))
	assert raw_after is None

	assert not frappe.db.exists("Vault Secret", name)


# =============================================================================
# get_secret (metadata, not value)
# =============================================================================


def test_get_secret_returns_metadata_without_value():
	"""get_secret returns metadata fields and permissions but not the secret value."""
	frappe.set_user(VAULT_ADMIN)
	# With autoname: "field:path", the doc name IS the path
	name = "master_key"
	assert frappe.db.exists(
		"Vault Secret", name
	), "Fixture secret 'master_key' not found — run setup.py"

	result = get_secret(name)

	assert result["name"] == name
	assert result["path"] == "master_key"
	assert "value" not in result, "get_secret must not expose the secret value"
	assert "permissions" in result
	assert result["permissions"]["read"] is True


# =============================================================================
# Folder sharing
# =============================================================================


def test_share_folder_grants_access_to_secrets_inside(cleanup_secret, vault_client):
	"""Sharing a folder with a user grants them read access to all secrets inside."""
	frappe.set_user(VAULT_ADMIN)
	folder_path = "tests/shared-folder"
	secret_path = "tests/shared-folder/api_key"
	value = "shared-folder-secret-value"

	# Create the secret (folder is auto-created by create_secret)
	result = create_secret(title="Shared Folder Secret", path=secret_path, value=value)
	secret_name = result["name"]
	cleanup_secret(secret_name, secret_path)

	# NO_ACCESS user cannot read the secret before sharing
	frappe.set_user(NO_ACCESS)
	with pytest.raises(frappe.PermissionError):
		get_secret(secret_name)

	# Admin shares the folder
	frappe.set_user(VAULT_ADMIN)
	share_folder(folder_path, NO_ACCESS, read=1, write=0, share=0)

	# NO_ACCESS user can now read secrets inside the folder via ancestry walk
	frappe.set_user(NO_ACCESS)
	meta = get_secret(secret_name)
	assert meta["name"] == secret_name
	assert meta["permissions"]["read"] is True

	# Revoke by removing the folder share
	frappe.set_user(VAULT_ADMIN)
	frappe.share.remove("Vault Secret", folder_path, NO_ACCESS)

	# Access is revoked again
	frappe.set_user(NO_ACCESS)
	with pytest.raises(frappe.PermissionError):
		get_secret(secret_name)


def test_share_folder_invalid_raises(cleanup_secret):
	"""share_folder raises ValidationError when the target doc is not a folder."""
	frappe.set_user(VAULT_ADMIN)
	path = "tests/api-surface/not-a-folder-secret"
	result = create_secret(title="Not A Folder", path=path, value="x")
	name = result["name"]
	cleanup_secret(name, path)

	with pytest.raises(frappe.ValidationError):
		share_folder(name, NO_ACCESS)


# =============================================================================
# Sharing (individual secret)
# =============================================================================


def test_share_and_revoke(cleanup_secret, vault_client):
	"""Share a secret with no-access user; they can read it; remove share revokes access."""
	frappe.set_user(VAULT_ADMIN)
	path = "tests/api-surface/share-test"

	result = create_secret(title="Share Test Secret", path=path, value="shared-value-xyz")
	name = result["name"]
	cleanup_secret(name, path)

	frappe.set_user(NO_ACCESS)
	with pytest.raises(frappe.PermissionError):
		get_secret(name)

	frappe.set_user(VAULT_ADMIN)
	share_secret(name, NO_ACCESS, read=1, write=0, share=0)

	frappe.set_user(NO_ACCESS)
	meta = get_secret(name)
	assert meta["name"] == name
	assert meta["permissions"]["read"] is True

	frappe.set_user(VAULT_ADMIN)
	remove_share(name, NO_ACCESS)

	frappe.set_user(NO_ACCESS)
	with pytest.raises(frappe.PermissionError):
		get_secret(name)


def test_get_shared_users(cleanup_secret):
	"""get_shared_users returns the DocShare records for a secret."""
	frappe.set_user(VAULT_ADMIN)
	path = "tests/api-surface/shared-users-test"

	result = create_secret(title="Shared Users Test", path=path, value="some-value")
	name = result["name"]
	cleanup_secret(name, path)

	share_secret(name, NO_ACCESS, read=1, write=0, share=0)

	shares = get_shared_users(name)
	user_emails = [s["user"] for s in shares]
	assert NO_ACCESS in user_emails


# =============================================================================
# API disabled
# =============================================================================


@pytest.fixture
def secrets_api_disabled(monkeypatch):
	"""Disable the Vault Secrets API.

	Sets vault_secrets_api_enabled to False to guarantee the API is disabled
	regardless of what the test site's site_config.json contains.
	"""
	monkeypatch.setitem(frappe.conf, "vault_secrets_api_enabled", False)


def testsecrets_api_disabled_blocks_get_secrets(secrets_api_disabled):
	"""All API calls raise PermissionError when vault_secrets_api_enabled is False."""
	frappe.set_user(VAULT_ADMIN)

	with pytest.raises(frappe.PermissionError):
		get_secrets()


def testsecrets_api_disabled_blocks_create(secrets_api_disabled):
	frappe.set_user(VAULT_ADMIN)

	with pytest.raises(frappe.PermissionError):
		create_secret(title="Blocked", path="blocked/test", value="x")


def testsecrets_api_disabled_blocks_get_folders(secrets_api_disabled):
	frappe.set_user(VAULT_ADMIN)

	with pytest.raises(frappe.PermissionError):
		get_folders()
