# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

"""
Integration tests for vault_sync — the active-active replication module.

These tests require two running OpenBao instances:
  - Primary:   BAO_ADDR / BAO_TOKEN        (default: http://localhost:8200 / test-token)
  - Secondary: BAO_ADDR_SECONDARY / BAO_TOKEN_SECONDARY (default: http://localhost:8201 / test-token)

In CI both are started as service containers. Locally, run two dev-mode instances:
  bao server -dev -dev-root-token-id=test-token -dev-listen-address=127.0.0.1:8200
  bao server -dev -dev-root-token-id=test-token -dev-listen-address=127.0.0.1:8201

Key design note — replication vs. failover
------------------------------------------
vault_sync provides *data replication*, not *high-availability failover*.
Writes go to local OpenBao synchronously, then fan out to remotes asynchronously
via RQ background jobs.  If local OpenBao is unavailable writes fail hard; the
remotes are write destinations, not fallback read sources.
"""

import os
import time

import frappe
import pytest

from frappe_vault.vault_client import (
	VaultClient,
	VaultConnectionError,
	VaultError,
	reset_vault_client,
)
from frappe_vault.vault_sync import (
	compare_and_sync,
	reconcile_all,
	reconcile_with_remote,
	remote_delete_job,
	remote_write_job,
	sync_delete,
	sync_write,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_client():
	reset_vault_client()
	yield
	reset_vault_client()


@pytest.fixture
def require_secondary(secondary_client):
	"""Skip the test if the secondary OpenBao instance is not reachable."""
	try:
		secondary_client.check_health()
	except VaultConnectionError as exc:
		pytest.skip(f"Secondary OpenBao instance not reachable: {exc}")


@pytest.fixture
def primary_client():
	return VaultClient()


def _secondary_url_and_token():
	configured = (frappe.conf.get("vault_remotes") or [{}])[0]
	url = os.environ.get("BAO_ADDR_SECONDARY") or configured.get("url") or "http://localhost:8201"
	token = (
		os.environ.get("BAO_TOKEN_SECONDARY")
		or configured.get("token")
		or frappe.conf.get("vault_token", "test-token")
	)
	return url, token


@pytest.fixture
def secondary_client():
	url, token = _secondary_url_and_token()
	return VaultClient(url=url, token=token)


@pytest.fixture
def patch_sync_enabled(monkeypatch):
	"""Enable vault_sync_enabled and point vault_remotes at the secondary."""
	url, token = _secondary_url_and_token()
	monkeypatch.setattr("frappe.conf.vault_sync_enabled", True)
	monkeypatch.setattr(
		"frappe.conf.vault_remotes", [{"name": "secondary", "url": url, "token": token}]
	)


@pytest.fixture
def cleanup_both(primary_client, secondary_client):
	"""Track secrets created during a test and delete them from both nodes afterwards."""
	created = []

	def track(doctype, name, fieldname):
		created.append((doctype, name, fieldname))

	yield track

	for doctype, name, fieldname in created:
		for client in (primary_client, secondary_client):
			try:
				client.delete_secret(doctype, name, fieldname)
			except VaultError:
				pass


# ---------------------------------------------------------------------------
# sync_write / sync_delete — unit-level with mocks
# ---------------------------------------------------------------------------


def test_sync_write_local_only_when_sync_disabled(primary_client, monkeypatch, cleanup_both):
	"""sync_write stores on local only when vault_sync_enabled is False."""
	cleanup_both("SyncTest", "local-only", "api_key")
	monkeypatch.setattr("frappe.conf.vault_sync_enabled", False)
	monkeypatch.setattr("frappe.conf.vault_remotes", [])

	sync_write("SyncTest", "local-only", "api_key", "secret-value")

	assert primary_client.get_secret("SyncTest", "local-only", "api_key") == "secret-value"


def test_sync_write_enqueues_remote_when_sync_enabled(
	primary_client, secondary_client, patch_sync_enabled, cleanup_both, monkeypatch, require_secondary
):
	"""sync_write writes to primary and enqueues a remote job; executing the job replicates."""
	cleanup_both("SyncTest", "enqueue-test", "api_key")

	enqueued = []

	def capture_enqueue(fn, **kwargs):
		enqueued.append({"fn": fn, "kwargs": kwargs})

	monkeypatch.setattr("frappe.enqueue", capture_enqueue)

	sync_write("SyncTest", "enqueue-test", "api_key", "replicated-value")

	# Primary must have the value immediately
	assert primary_client.get_secret("SyncTest", "enqueue-test", "api_key") == "replicated-value"

	# One remote job should have been enqueued
	assert len(enqueued) == 1
	assert enqueued[0]["fn"] == "frappe_vault.vault_sync.remote_write_job"
	assert enqueued[0]["kwargs"]["remote_name"] == "secondary"


def test_remote_write_job_replicates_to_secondary(
	secondary_client, patch_sync_enabled, cleanup_both, require_secondary
):
	"""remote_write_job directly replicates a secret to the secondary node."""
	cleanup_both("SyncTest", "remote-write", "token")

	path = "SyncTest/remote-write/token"
	remote_write_job("secondary", path, {"value": "remote-secret"})

	result = secondary_client.get_secret_with_metadata(path)
	assert result is not None
	assert result.get("data", {}).get("value") == "remote-secret"


def test_remote_delete_job_removes_from_secondary(
	secondary_client, patch_sync_enabled, cleanup_both, require_secondary
):
	"""remote_delete_job removes a secret from the secondary node."""
	cleanup_both("SyncTest", "remote-delete", "token")

	path = "SyncTest/remote-delete/token"
	secondary_client.set_secret_raw(path, {"value": "to-be-deleted"})

	remote_delete_job("secondary", path)

	assert secondary_client.get_secret_with_metadata(path) is None


def test_remote_write_job_skips_unknown_remote(monkeypatch):
	"""remote_write_job logs and returns (no exception) for unknown remote names."""
	monkeypatch.setattr("frappe.conf.vault_remotes", [])
	logged = []
	monkeypatch.setattr("frappe.log_error", lambda msg, title=None: logged.append(msg))

	# Should not raise
	remote_write_job("nonexistent", "some/path", {"value": "x"})
	assert any("nonexistent" in str(m) for m in logged)


def test_remote_write_job_reraises_vault_error(patch_sync_enabled, monkeypatch):
	"""remote_write_job re-raises VaultError so RQ can retry the job."""
	monkeypatch.setattr("frappe.log_error", lambda *a, **kw: None)

	# Point the secondary at an unreachable address
	monkeypatch.setattr(
		"frappe.conf.vault_remotes",
		[{"name": "secondary", "url": "http://localhost:9999", "token": "bad"}],
	)

	with pytest.raises(VaultError):
		remote_write_job("secondary", "some/path", {"value": "x"})


# ---------------------------------------------------------------------------
# sync_delete
# ---------------------------------------------------------------------------


def test_sync_delete_local_only_when_sync_disabled(primary_client, monkeypatch, cleanup_both):
	"""sync_delete removes from local only when vault_sync_enabled is False."""
	cleanup_both("SyncTest", "delete-local", "key")
	monkeypatch.setattr("frappe.conf.vault_sync_enabled", False)
	monkeypatch.setattr("frappe.conf.vault_remotes", [])

	primary_client.set_secret("SyncTest", "delete-local", "key", "value")
	sync_delete("SyncTest", "delete-local", "key")

	assert primary_client.get_secret("SyncTest", "delete-local", "key") is None


def test_sync_delete_enqueues_remote_when_sync_enabled(
	primary_client, patch_sync_enabled, cleanup_both, monkeypatch, require_secondary
):
	"""sync_delete removes from local and enqueues a remote delete job."""
	cleanup_both("SyncTest", "delete-both", "key")
	primary_client.set_secret("SyncTest", "delete-both", "key", "value")

	enqueued = []
	monkeypatch.setattr("frappe.enqueue", lambda fn, **kw: enqueued.append(fn))

	sync_delete("SyncTest", "delete-both", "key")

	assert primary_client.get_secret("SyncTest", "delete-both", "key") is None
	assert len(enqueued) == 1


# ---------------------------------------------------------------------------
# compare_and_sync
# ---------------------------------------------------------------------------


def test_compare_and_sync_pushes_local_only_secret(
	primary_client, secondary_client, cleanup_both, require_secondary
):
	"""compare_and_sync pushes a secret that exists only on local to the remote."""
	cleanup_both("SyncTest", "push-only", "field")

	primary_client.set_secret("SyncTest", "push-only", "field", "push-value")
	path = primary_client._get_secret_path("SyncTest", "push-only", "field").replace(
		"/v1/secret/data/", ""
	)

	result = compare_and_sync(primary_client, secondary_client, path)

	assert result == "pushed"
	assert secondary_client.get_secret("SyncTest", "push-only", "field") == "push-value"


def test_compare_and_sync_pulls_remote_only_secret(
	primary_client, secondary_client, cleanup_both, require_secondary
):
	"""compare_and_sync pulls a secret that exists only on the remote to local."""
	cleanup_both("SyncTest", "pull-only", "field")

	secondary_client.set_secret("SyncTest", "pull-only", "field", "pull-value")
	path = secondary_client._get_secret_path("SyncTest", "pull-only", "field").replace(
		"/v1/secret/data/", ""
	)

	result = compare_and_sync(primary_client, secondary_client, path)

	assert result == "pulled"
	assert primary_client.get_secret("SyncTest", "pull-only", "field") == "pull-value"


def test_compare_and_sync_skips_when_in_sync(
	primary_client, secondary_client, cleanup_both, require_secondary
):
	"""compare_and_sync returns 'skipped' when both nodes have the same version."""
	cleanup_both("SyncTest", "in-sync", "field")

	value = "shared-value"
	primary_client.set_secret("SyncTest", "in-sync", "field", value)
	secondary_client.set_secret("SyncTest", "in-sync", "field", value)

	# Brief pause to let KV metadata timestamps settle
	time.sleep(0.1)

	path = primary_client._get_secret_path("SyncTest", "in-sync", "field").replace(
		"/v1/secret/data/", ""
	)

	result = compare_and_sync(primary_client, secondary_client, path)
	# Equal timestamps → skipped
	assert result == "skipped"


def test_compare_and_sync_last_write_wins(
	primary_client, secondary_client, cleanup_both, require_secondary
):
	"""compare_and_sync uses last-write-wins when both nodes have the secret."""
	cleanup_both("SyncTest", "lww-test", "field")

	primary_client.set_secret("SyncTest", "lww-test", "field", "older-value")
	time.sleep(0.5)
	secondary_client.set_secret("SyncTest", "lww-test", "field", "newer-value")

	path = primary_client._get_secret_path("SyncTest", "lww-test", "field").replace(
		"/v1/secret/data/", ""
	)

	result = compare_and_sync(primary_client, secondary_client, path)

	assert result == "pulled"
	assert primary_client.get_secret("SyncTest", "lww-test", "field") == "newer-value"


# ---------------------------------------------------------------------------
# reconcile_all
# ---------------------------------------------------------------------------


def test_reconcile_all_skips_when_sync_disabled(monkeypatch):
	"""reconcile_all returns skipped status when vault_sync_enabled is False."""
	monkeypatch.setattr("frappe.conf.vault_sync_enabled", False)
	monkeypatch.setattr("frappe.conf.vault_remotes", [])

	result = reconcile_all()
	assert result["status"] == "skipped"


def test_reconcile_all_reports_per_remote_stats(
	primary_client, secondary_client, patch_sync_enabled, cleanup_both, require_secondary
):
	"""reconcile_all runs and returns structured stats for each configured remote."""
	cleanup_both("SyncTest", "reconcile-test", "field")
	primary_client.set_secret("SyncTest", "reconcile-test", "field", "reconcile-value")

	result = reconcile_all()

	assert result["status"] == "completed"
	assert "secondary" in result["remotes"]
	remote_stats = result["remotes"]["secondary"]
	assert remote_stats["status"] == "completed"
	assert isinstance(remote_stats.get("pushed", 0), int)
	assert isinstance(remote_stats.get("pulled", 0), int)


def test_reconcile_all_handles_unreachable_remote(monkeypatch):
	"""reconcile_all logs the error and continues when a remote is unreachable."""
	monkeypatch.setattr("frappe.conf.vault_sync_enabled", True)
	monkeypatch.setattr(
		"frappe.conf.vault_remotes",
		[{"name": "dead-remote", "url": "http://localhost:9999", "token": "bad"}],
	)
	monkeypatch.setattr("frappe.log_error", lambda *a, **kw: None)

	result = reconcile_all()

	assert result["status"] == "completed"
	assert "dead-remote" in result["remotes"]
	assert result["remotes"]["dead-remote"]["status"] == "error"
