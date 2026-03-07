# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

"""
Integration tests that run against a real OpenBao instance.

Requires OpenBao to be running. URL and token are read from (in priority order):
  1. BAO_ADDR / BAO_TOKEN environment variables
  2. VAULT_ADDR / VAULT_TOKEN environment variables
  3. vault_url / vault_token in site_config / common_site_config.json
"""

import frappe
import pytest
from frappe.utils.password import (
	check_password,
	get_decrypted_password,
	set_encrypted_password,
	update_password,
)

from frappe_vault.tests.fixtures import users
from frappe_vault.vault_client import VaultClient, VaultError, reset_vault_client


@pytest.fixture(autouse=True)
def reset_client():
	"""Reset the module-level client before each test."""
	reset_vault_client()
	yield
	reset_vault_client()


@pytest.fixture
def vault_client():
	"""Get an OpenBao client using site config (env vars override if set)."""
	return VaultClient()


@pytest.fixture
def cleanup_secrets(vault_client):
	"""Track and cleanup secrets created during tests."""
	created_secrets = []

	def track(doctype, name, fieldname):
		created_secrets.append((doctype, name, fieldname))

	yield track

	for doctype, name, fieldname in created_secrets:
		try:
			vault_client.delete_secret(doctype, name, fieldname)
		except VaultError:
			pass


# =============================================================================
# OpenBao Client Integration Tests
# =============================================================================


def test_openbao_health_check(vault_client):
	"""Test that OpenBao health check returns valid response."""
	health = vault_client.check_health()
	assert "initialized" in health


def test_openbao_is_available(vault_client):
	"""Test that OpenBao is available and responding."""
	assert vault_client.is_available() is True


def test_secret_roundtrip(vault_client, cleanup_secrets):
	"""Test storing and retrieving a secret."""
	cleanup_secrets("Test", "integration-test", "api_key")
	vault_client.set_secret("Test", "integration-test", "api_key", "super-secret-value")
	retrieved = vault_client.get_secret("Test", "integration-test", "api_key")
	assert retrieved == "super-secret-value"


def test_secret_update(vault_client, cleanup_secrets):
	"""Test updating an existing secret."""
	cleanup_secrets("Test", "update-test", "password")
	vault_client.set_secret("Test", "update-test", "password", "initial-value")
	vault_client.set_secret("Test", "update-test", "password", "updated-value")
	retrieved = vault_client.get_secret("Test", "update-test", "password")
	assert retrieved == "updated-value"


def test_secret_delete(vault_client, cleanup_secrets):
	"""Test deleting a secret."""
	vault_client.set_secret("Test", "delete-test", "secret", "to-be-deleted")
	vault_client.delete_secret("Test", "delete-test", "secret")
	retrieved = vault_client.get_secret("Test", "delete-test", "secret")
	assert retrieved is None


def test_secret_not_found(vault_client):
	"""Test retrieving a non-existent secret."""
	retrieved = vault_client.get_secret("Nonexistent", "doc", "field")
	assert retrieved is None


def test_special_characters_in_path(vault_client, cleanup_secrets):
	"""Test handling of special characters in doctype/name/fieldname."""
	cleanup_secrets("Test DocType", "user@example.com", "api_key")
	vault_client.set_secret("Test DocType", "user@example.com", "api_key", "special-secret")
	retrieved = vault_client.get_secret("Test DocType", "user@example.com", "api_key")
	assert retrieved == "special-secret"


# =============================================================================
# Password Function Integration Tests
# =============================================================================


def test_encrypted_password_patches_are_active():
	"""Verify the monkey-patches from frappe_vault.__init__ are in place."""

	assert "frappe_vault" in set_encrypted_password.__module__
	assert "frappe_vault" in get_decrypted_password.__module__


def test_user_password_patches_are_active():

	assert "frappe_vault" in update_password.__module__
	assert "frappe_vault" in check_password.__module__


def test_user_password_via_openbao(vault_client, cleanup_secrets):
	"""update_password stores a bcrypt hash in OpenBao; check_password verifies it."""

	cleanup_secrets("User", "vault-test-user@vault.test", "password")
	update_password("vault-test-user@vault.test", "test-password-123", "User", "password")

	stored_hash = vault_client.get_secret("User", "vault-test-user@vault.test", "password")
	assert stored_hash is not None
	assert stored_hash.startswith("$")

	result = check_password("vault-test-user@vault.test", "test-password-123", "User", "password")
	assert result == "vault-test-user@vault.test"


def test_user_password_wrong_password(vault_client, cleanup_secrets):
	"""Wrong password raises AuthenticationError."""

	cleanup_secrets("User", "vault-wrong-pw@vault.test", "password")
	update_password("vault-wrong-pw@vault.test", "correct-password", "User", "password")

	with pytest.raises(frappe.AuthenticationError):
		check_password("vault-wrong-pw@vault.test", "wrong-password", "User", "password")


def test_no_password_in_auth_table_when_openbao_enabled(vault_client, cleanup_secrets):
	"""Passwords must NOT be stored in __Auth when OpenBao user passwords are enabled."""

	cleanup_secrets("User", "vault-no-db@vault.test", "password")
	update_password("vault-no-db@vault.test", "my-password", "User", "password")

	Auth = frappe.qb.Table("__Auth")
	result = (
		frappe.qb.from_(Auth)
		.select(Auth.star)
		.where(Auth.doctype == "User")
		.where(Auth.name == "vault-no-db@vault.test")
		.where(Auth.fieldname == "password")
		.run(as_dict=True)
	)
	assert len(result) == 0, "Password should not be in __Auth when OpenBao is enabled"

	vault_hash = vault_client.get_secret("User", "vault-no-db@vault.test", "password")
	assert vault_hash is not None


# =============================================================================
# Migration verification tests — passwords created by setup.py
# =============================================================================


def test_user_passwords_migrated_to_openbao(vault_client):
	"""Users created with vault OFF in setup.py must have their hashes in OpenBao after migration."""
	for u in users:
		stored = vault_client.get_secret("User", u["email"], "password")
		assert stored is not None, f"Password for {u['email']} was not migrated to OpenBao"
		assert stored.startswith("$"), f"Expected bcrypt hash for {u['email']}, got: {stored!r}"


def test_migrated_passwords_authenticate():
	"""Migrated bcrypt hashes must pass check_password against the original plaintext."""

	for u in users:
		result = check_password(u["email"], u["password"])
		assert result == u["email"], f"Authentication failed for {u['email']}"
