# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

"""
HTTP integration tests for the /v1/* proxy endpoint.

Requires both Frappe and OpenBao to be running.

FRAPPE_URL defaults to the webserver_port in common_site_config.json (http://localhost:8004).
Auth credentials are generated automatically by the session fixture using the
already-initialised Frappe instance — no env vars needed.
"""

import os

import frappe
import pytest
import requests

FRAPPE_URL = os.environ.get("FRAPPE_URL", "http://localhost:8004")

_TEST_SECRET_PATH = "secret/data/frappe/http-test/pytest/api_key"
_TEST_METADATA_PATH = "secret/metadata/frappe/http-test/pytest/api_key"


def _v1_url(path: str) -> str:
	path = path.lstrip("/")
	return f"{FRAPPE_URL}/v1/{path}"


@pytest.fixture(scope="module")
def api_keys(db_instance):
	"""Generate fresh API keys for Administrator and commit them to the live DB."""
	from frappe.core.doctype.user.user import generate_keys

	keys = generate_keys("Administrator")
	# frappe.db.commit is mocked in db_instance; use raw SQL so the HTTP server
	# can immediately validate the newly-written keys.
	frappe.db.sql("COMMIT")
	return keys


@pytest.fixture(scope="module")
def session(api_keys):
	"""Authenticated session using Frappe's Authorization header."""
	s = requests.Session()
	s.headers.update(
		{
			"Authorization": f"token {api_keys['api_key']}:{api_keys['api_secret']}",
			"Content-Type": "application/json",
		}
	)
	return s


@pytest.fixture(scope="module")
def vault_token_session(api_keys):
	"""Authenticated session using the native X-Vault-Token header.

	The token value is api_key:api_secret — the same Frappe credentials,
	delivered via the header that the Vault CLI and hvac use natively.
	"""
	s = requests.Session()
	s.headers.update(
		{
			"X-Vault-Token": f"{api_keys['api_key']}:{api_keys['api_secret']}",
			"Content-Type": "application/json",
		}
	)
	return s


# ---------------------------------------------------------------------------
# Auth / access-control
# ---------------------------------------------------------------------------


def test_unauthenticated_request_returns_401():
	"""Bare request with no credentials must be rejected with 401 JSON."""
	resp = requests.get(_v1_url("sys/health"), allow_redirects=False)
	assert resp.status_code == 401
	body = resp.json()
	assert "errors" in body


def test_authenticated_health_check(session):
	"""Authenticated user should reach the health endpoint successfully."""
	resp = session.get(_v1_url("sys/health"))
	assert resp.status_code == 200
	body = resp.json()
	assert "initialized" in body
	assert "sealed" in body


def test_x_vault_token_auth(vault_token_session):
	"""X-Vault-Token header (native Vault/OpenBao auth) must be accepted."""
	resp = vault_token_session.get(_v1_url("sys/health"))
	assert resp.status_code == 200
	body = resp.json()
	assert "initialized" in body
	assert "sealed" in body


def test_x_vault_token_write_read_roundtrip(vault_token_session):
	"""A client using X-Vault-Token must be able to write and read secrets."""
	path = "secret/data/frappe/http-test/vault-token-auth"
	vault_token_session.delete(_v1_url(f"secret/metadata/frappe/http-test/vault-token-auth"))
	write = vault_token_session.post(_v1_url(path), json={"data": {"value": "vault-native"}})
	assert write.status_code == 200
	read = vault_token_session.get(_v1_url(path))
	assert read.status_code == 200
	assert read.json()["data"]["data"]["value"] == "vault-native"
	vault_token_session.delete(_v1_url(f"secret/metadata/frappe/http-test/vault-token-auth"))


# ---------------------------------------------------------------------------
# Blocked endpoints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
	"blocked_path",
	[
		"sys/seal",
		"sys/unseal",
		"sys/init",
		"sys/rekey",
		"sys/rotate",
		"auth/token/create",
		"auth/token/revoke",
	],
)
def test_blocked_paths_return_403(session, blocked_path):
	"""Sensitive system paths must be blocked regardless of caller."""
	resp = session.get(_v1_url(blocked_path))
	assert resp.status_code == 403
	body = resp.json()
	assert "errors" in body


# ---------------------------------------------------------------------------
# Secret round-trip
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def cleanup_test_secret(session):
	"""Wipe the test secret before and after every test for isolation."""
	session.delete(_v1_url(_TEST_METADATA_PATH))
	yield
	session.delete(_v1_url(_TEST_METADATA_PATH))


def test_write_secret(session):
	"""POST to a KV v2 data path should persist the secret and return version info."""
	resp = session.post(_v1_url(_TEST_SECRET_PATH), json={"data": {"value": "s3cr3t-value"}})
	assert resp.status_code == 200
	assert resp.json().get("data", {}).get("version", 0) >= 1


def test_read_secret(session):
	"""GET after POST must return the stored value."""
	session.post(_v1_url(_TEST_SECRET_PATH), json={"data": {"value": "hello-vault"}})
	resp = session.get(_v1_url(_TEST_SECRET_PATH))
	assert resp.status_code == 200
	assert resp.json()["data"]["data"]["value"] == "hello-vault"


def test_read_nonexistent_secret_returns_404(session):
	resp = session.get(_v1_url("secret/data/frappe/http-test/does-not-exist/field"))
	assert resp.status_code == 404


def test_delete_secret(session):
	"""DELETE on the metadata path should make the secret unreadable."""
	session.post(_v1_url(_TEST_SECRET_PATH), json={"data": {"value": "to-be-deleted"}})
	del_resp = session.delete(_v1_url(_TEST_METADATA_PATH))
	assert del_resp.status_code in (200, 204)
	assert session.get(_v1_url(_TEST_SECRET_PATH)).status_code == 404


def test_secret_update_increments_version(session):
	"""Writing to the same path twice must increment the KV v2 version number."""
	resp1 = session.post(_v1_url(_TEST_SECRET_PATH), json={"data": {"value": "v1"}})
	resp2 = session.post(_v1_url(_TEST_SECRET_PATH), json={"data": {"value": "v2"}})
	assert resp1.status_code == 200
	assert resp2.status_code == 200
	assert resp2.json()["data"]["version"] == resp1.json()["data"]["version"] + 1
	assert session.get(_v1_url(_TEST_SECRET_PATH)).json()["data"]["data"]["value"] == "v2"


def test_invalid_json_body_is_rejected(session):
	"""Malformed JSON body on a POST must be rejected.

	Frappe's make_form_dict() parses JSON before the page renderer runs, so the
	error surfaces as 500 rather than 400.  Either status confirms the request
	was rejected rather than silently accepted.
	"""
	resp = session.post(
		_v1_url(_TEST_SECRET_PATH),
		data=b"not-json",
		headers={"Content-Type": "application/json"},
	)
	assert resp.status_code in (400, 500)
