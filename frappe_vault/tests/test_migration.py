# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

"""
Tests for migrating existing Frappe secrets to OpenBao.

Two distinct password storage paths exist in frappe_vault:

  1. User login passwords  — bcrypt hashes stored via update_password()
  2. Encrypted Password fields — arbitrary doctypes with fieldtype=Password,
     stored via set_encrypted_password() / retrieved via get_decrypted_password()

setup.py creates test users with vault OFF so their hashes land in __Auth,
then calls migrate_passwords_to_vault(). These tests verify that migration
and confirm the non-user password field path works end-to-end.

Requires a running OpenBao instance (BAO_TOKEN / VAULT_TOKEN).
"""

import frappe
import pytest
from frappe.utils.password import get_decrypted_password, set_encrypted_password

from frappe_vault import original_set_encrypted_password
from frappe_vault.install import migrate_passwords_to_vault
from frappe_vault.vault_client import VaultClient, VaultError, reset_vault_client

# Use Social Login Key as the test doctype — it is always present in Frappe
# and its `client_secret` field has fieldtype=Password, so is_field_vault_enabled
# returns True when vault_password_fields_enabled is set.
_TEST_DOCTYPE = "Social Login Key"
_TEST_FIELD = "client_secret"
_TEST_DOC_NAME = "vault-migr-test-provider"
_TEST_SECRET_VALUE = "migr-test-client-secret-abc123"


@pytest.fixture(autouse=True)
def reset_client():
	reset_vault_client()
	yield
	reset_vault_client()


@pytest.fixture
def vault_client():
	return VaultClient()


@pytest.fixture
def cleanup_auth_and_vault(vault_client):
	"""Remove the test entry from __Auth and OpenBao after each test."""
	yield
	frappe.db.delete(
		"__Auth",
		{"doctype": _TEST_DOCTYPE, "name": _TEST_DOC_NAME, "fieldname": _TEST_FIELD},
	)
	try:
		vault_client.delete_secret(_TEST_DOCTYPE, _TEST_DOC_NAME, _TEST_FIELD)
	except VaultError:
		pass


# =============================================================================
# Non-user Password field — fresh write with vault enabled
# =============================================================================


def test_set_encrypted_password_stores_plaintext_in_vault(
	monkeypatch, vault_client, cleanup_auth_and_vault
):
	"""With vault_password_fields_enabled on, set_encrypted_password stores plaintext in vault.

	The patched function routes Password-fieldtype fields to OpenBao via sync_write,
	bypassing __Auth entirely. get_decrypted_password then reads directly from vault.
	"""
	monkeypatch.setitem(frappe.conf, "vault_password_fields_enabled", True)

	set_encrypted_password(_TEST_DOCTYPE, _TEST_DOC_NAME, _TEST_SECRET_VALUE, _TEST_FIELD)

	# Value is in OpenBao as plaintext
	stored = vault_client.get_secret(_TEST_DOCTYPE, _TEST_DOC_NAME, _TEST_FIELD)
	assert stored == _TEST_SECRET_VALUE

	# Patched get_decrypted_password reads it back from vault
	retrieved = get_decrypted_password(
		_TEST_DOCTYPE, _TEST_DOC_NAME, _TEST_FIELD, raise_exception=False
	)
	assert retrieved == _TEST_SECRET_VALUE

	# __Auth table must be empty for this entry — vault is the source of truth
	Auth = frappe.qb.Table("__Auth")
	auth_rows = (
		frappe.qb.from_(Auth)
		.select(Auth.star)
		.where(Auth.doctype == _TEST_DOCTYPE)
		.where(Auth.name == _TEST_DOC_NAME)
		.where(Auth.fieldname == _TEST_FIELD)
		.run(as_dict=True)
	)
	assert len(auth_rows) == 0, "Password should not be in __Auth when vault is enabled"


# =============================================================================
# Non-user Password field — migration from __Auth to OpenBao
# =============================================================================


def test_non_user_password_field_migration(monkeypatch, vault_client, cleanup_auth_and_vault):
	"""A Password field written to __Auth while vault was off migrates to OpenBao.

	original_set_encrypted_password bypasses the monkey-patch so the value lands
	in __Auth as a Fernet-encrypted blob (normal Frappe behavior). After migration
	that encrypted blob is present in OpenBao at the standard namespaced path.
	"""
	monkeypatch.setitem(frappe.conf, "vault_password_fields_enabled", False)

	# Write directly to __Auth, bypassing the vault patch
	original_set_encrypted_password(_TEST_DOCTYPE, _TEST_DOC_NAME, _TEST_SECRET_VALUE, _TEST_FIELD)

	# Confirm the value is in __Auth
	Auth = frappe.qb.Table("__Auth")
	auth_rows = (
		frappe.qb.from_(Auth)
		.select(Auth.star)
		.where(Auth.doctype == _TEST_DOCTYPE)
		.where(Auth.name == _TEST_DOC_NAME)
		.where(Auth.fieldname == _TEST_FIELD)
		.run(as_dict=True)
	)
	assert len(auth_rows) == 1, "Expected password in __Auth before migration"

	# Enable vault and migrate
	monkeypatch.setitem(frappe.conf, "vault_password_fields_enabled", True)
	monkeypatch.setitem(frappe.conf, "enable_vault_user_passwords", True)

	stats = migrate_passwords_to_vault(skip_backup=True)

	assert stats.get("migrated", 0) >= 1, f"Expected at least 1 migration, got: {stats}"

	# The (Fernet-encrypted) value is now in OpenBao
	stored = vault_client.get_secret(_TEST_DOCTYPE, _TEST_DOC_NAME, _TEST_FIELD)
	assert stored is not None, "Migrated value not found in OpenBao"


def test_migration_skips_already_migrated(monkeypatch, vault_client, cleanup_auth_and_vault):
	"""Running migration a second time does not overwrite values already in OpenBao."""
	monkeypatch.setitem(frappe.conf, "vault_password_fields_enabled", True)
	monkeypatch.setitem(frappe.conf, "enable_vault_user_passwords", True)

	# Write to __Auth directly
	original_set_encrypted_password(_TEST_DOCTYPE, _TEST_DOC_NAME, _TEST_SECRET_VALUE, _TEST_FIELD)

	# First migration
	first = migrate_passwords_to_vault(skip_backup=True)
	assert first.get("migrated", 0) >= 1

	# Second migration — same entry is already in vault
	second = migrate_passwords_to_vault(skip_backup=True)
	assert second.get("already_exists", 0) >= 1
	assert second.get("migrated", 0) == 0


# =============================================================================
# Migration statistics
# =============================================================================


def test_migration_stats_structure(monkeypatch):
	"""migrate_passwords_to_vault returns a dict with the expected keys."""
	monkeypatch.setitem(frappe.conf, "vault_password_fields_enabled", True)
	monkeypatch.setitem(frappe.conf, "enable_vault_user_passwords", True)

	stats = migrate_passwords_to_vault(skip_backup=True)

	assert isinstance(stats, dict)
	# Either migrated/failed/already_exists (ran successfully) or skipped (early exit)
	assert "migrated" in stats


def test_migration_skips_when_vault_not_enabled(monkeypatch):
	"""Migration aborts early and returns skipped=True when vault is not configured."""
	monkeypatch.setitem(frappe.conf, "vault_password_fields_enabled", False)
	monkeypatch.setitem(frappe.conf, "enable_vault_user_passwords", False)

	stats = migrate_passwords_to_vault(skip_backup=True)

	assert stats.get("skipped") is True
	assert "vault_not_enabled" in stats.get("reason", "")
