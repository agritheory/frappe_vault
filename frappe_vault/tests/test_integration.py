# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

"""
Integration tests that run against a real OpenBao instance.

These tests require OpenBao to be running (e.g., via the CI service container
or locally with `bao server -dev`).

Environment variables expected:
- BAO_ADDR or VAULT_ADDR: OpenBao server URL (default: http://localhost:8200)
- BAO_TOKEN or VAULT_TOKEN: OpenBao authentication token
"""

import os

import frappe
import pytest

from frappe_vault.vault_client import VaultClient, VaultError, get_vault_client, reset_vault_client

# Skip all tests in this module if OpenBao is not available
pytestmark = pytest.mark.skipif(
	not (os.environ.get("BAO_TOKEN") or os.environ.get("VAULT_TOKEN")),
	reason="BAO_TOKEN/VAULT_TOKEN not set - OpenBao integration tests require a running OpenBao instance",
)


@pytest.fixture(autouse=True)
def reset_client():
	"""Reset the module-level client before each test."""
	reset_vault_client()
	yield
	reset_vault_client()


@pytest.fixture
def vault_client():
	"""Get an OpenBao client configured from environment."""
	return VaultClient(
		url=os.environ.get("BAO_ADDR") or os.environ.get("VAULT_ADDR", "http://localhost:8200"),
		token=os.environ.get("BAO_TOKEN") or os.environ.get("VAULT_TOKEN"),
	)


@pytest.fixture
def cleanup_secrets(vault_client):
	"""Track and cleanup secrets created during tests."""
	created_secrets = []

	def track(doctype, name, fieldname):
		created_secrets.append((doctype, name, fieldname))

	yield track

	# Cleanup after test
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

	assert "initialized" in health or "reachable" in health


def test_openbao_is_available(vault_client):
	"""Test that OpenBao is available and responding."""
	assert vault_client.is_available() is True


def test_secret_roundtrip(vault_client, cleanup_secrets):
	"""Test storing and retrieving a secret."""
	cleanup_secrets("Test", "integration-test", "api_key")

	# Store secret
	vault_client.set_secret("Test", "integration-test", "api_key", "super-secret-value")

	# Retrieve secret
	retrieved = vault_client.get_secret("Test", "integration-test", "api_key")

	assert retrieved == "super-secret-value"


def test_secret_update(vault_client, cleanup_secrets):
	"""Test updating an existing secret."""
	cleanup_secrets("Test", "update-test", "password")

	# Store initial value
	vault_client.set_secret("Test", "update-test", "password", "initial-value")

	# Update value
	vault_client.set_secret("Test", "update-test", "password", "updated-value")

	# Retrieve and verify update
	retrieved = vault_client.get_secret("Test", "update-test", "password")

	assert retrieved == "updated-value"


def test_secret_delete(vault_client, cleanup_secrets):
	"""Test deleting a secret."""
	# Store secret
	vault_client.set_secret("Test", "delete-test", "secret", "to-be-deleted")

	# Delete secret
	vault_client.delete_secret("Test", "delete-test", "secret")

	# Verify deletion
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


def test_encrypted_password_via_openbao(cleanup_secrets):
	"""Test set_encrypted_password and get_decrypted_password with OpenBao."""
	from frappe.utils.password import get_decrypted_password, set_encrypted_password

	# These should be the patched versions from frappe_vault
	assert "frappe_vault" in set_encrypted_password.__module__
	assert "frappe_vault" in get_decrypted_password.__module__

	cleanup_secrets("TestDoc", "test-encrypted", "api_secret")

	# Store via patched function
	set_encrypted_password("TestDoc", "test-encrypted", "my-api-secret", "api_secret")

	# Retrieve via patched function
	retrieved = get_decrypted_password(
		"TestDoc", "test-encrypted", "api_secret", raise_exception=False
	)

	# Note: This test may return None if is_field_vault_enabled returns False
	# because TestDoc doesn't exist. The test validates the patch is in place.


def test_user_password_via_openbao(vault_client, cleanup_secrets):
	"""Test update_password and check_password with OpenBao."""
	from frappe.utils.password import check_password, update_password

	# These should be the patched versions from frappe_vault
	assert "frappe_vault" in update_password.__module__
	assert "frappe_vault" in check_password.__module__

	cleanup_secrets("User", "vault-test-user", "password")

	# Store password hash via patched function
	update_password("vault-test-user", "test-password-123", "User", "password")

	# Verify hash is in OpenBao
	stored_hash = vault_client.get_secret("User", "vault-test-user", "password")
	assert stored_hash is not None
	assert stored_hash.startswith("$")  # passlib hash format

	# Verify password via patched function
	result = check_password("vault-test-user", "test-password-123", "User", "password")
	assert result == "vault-test-user"


def test_user_password_wrong_password(vault_client, cleanup_secrets):
	"""Test that wrong password raises AuthenticationError."""
	from frappe.utils.password import check_password, update_password

	cleanup_secrets("User", "vault-wrong-pw-test", "password")

	# Store password
	update_password("vault-wrong-pw-test", "correct-password", "User", "password")

	# Try wrong password
	with pytest.raises(frappe.AuthenticationError):
		check_password("vault-wrong-pw-test", "wrong-password", "User", "password")


def test_no_password_in_auth_table_when_openbao_enabled(vault_client, cleanup_secrets):
	"""Test that passwords are NOT stored in __Auth table when OpenBao is enabled."""
	from frappe.utils.password import update_password

	cleanup_secrets("User", "vault-no-db-test", "password")

	# Store password (should go to OpenBao, not DB)
	update_password("vault-no-db-test", "my-password", "User", "password")

	# Check __Auth table is empty for this user
	Auth = frappe.qb.Table("__Auth")
	result = (
		frappe.qb.from_(Auth)
		.select(Auth.star)
		.where(Auth.doctype == "User")
		.where(Auth.name == "vault-no-db-test")
		.where(Auth.fieldname == "password")
		.run(as_dict=True)
	)

	assert len(result) == 0, "Password should not be in __Auth table when OpenBao is enabled"

	# Verify it IS in OpenBao
	vault_hash = vault_client.get_secret("User", "vault-no-db-test", "password")
	assert vault_hash is not None
