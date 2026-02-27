# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

"""
Multi-site OpenBao synchronization module.

This module provides bidirectional sync between local and remote OpenBao instances
in an active-active multi-site deployment. It uses:

- Synchronous writes to local OpenBao
- Async RQ jobs for remote writes with retry on failure
- Hourly reconciliation using KV v2 metadata timestamps
- Last-write-wins conflict resolution
"""

from datetime import datetime
from typing import Any

import frappe
from frappe.utils import now_datetime

from frappe_vault.vault_client import (
	VaultClient,
	VaultConnectionError,
	VaultError,
	get_remote_clients,
	get_vault_client,
	is_sync_enabled,
)


def sync_write(doctype: str, name: str, fieldname: str, value: str) -> None:
	"""
	Write a secret to local OpenBao and enqueue async writes to all remotes.

	This is the primary write function for multi-site sync. It:
	1. Writes synchronously to local OpenBao (fails fast if local is down)
	2. Enqueues background jobs to write to each remote (fire-and-forget with retry)

	Args:
	    doctype: Frappe DocType
	    name: Document name
	    fieldname: Field name
	    value: The secret value to store

	Raises:
	    VaultConnectionError: If local OpenBao is unreachable
	    VaultAuthError: If local authentication fails
	    VaultError: For other local OpenBao errors
	"""
	# Write to local OpenBao synchronously
	local_client = get_vault_client()
	local_client.set_secret(doctype, name, fieldname, value)

	# If sync is not enabled, we're done
	if not is_sync_enabled():
		return

	# Build the path for remote writes
	path = f"frappe/{doctype}/{name}/{fieldname}"
	data = {"value": value}

	# Enqueue async writes to all remotes
	remote_clients = get_remote_clients()
	for remote_name in remote_clients:
		enqueue_remote_write(remote_name, path, data)


def sync_delete(doctype: str, name: str, fieldname: str) -> None:
	"""
	Delete a secret from local OpenBao and enqueue async deletes to all remotes.

	Args:
	    doctype: Frappe DocType
	    name: Document name
	    fieldname: Field name

	Raises:
	    VaultConnectionError: If local OpenBao is unreachable
	    VaultAuthError: If local authentication fails
	    VaultError: For other local OpenBao errors
	"""
	# Delete from local OpenBao synchronously
	local_client = get_vault_client()
	local_client.delete_secret(doctype, name, fieldname)

	# If sync is not enabled, we're done
	if not is_sync_enabled():
		return

	# Build the path for remote deletes
	path = f"frappe/{doctype}/{name}/{fieldname}"

	# Enqueue async deletes to all remotes
	remote_clients = get_remote_clients()
	for remote_name in remote_clients:
		enqueue_remote_delete(remote_name, path)


def enqueue_remote_write(remote_name: str, path: str, data: dict[str, Any]) -> None:
	"""
	Enqueue a background job to write to a remote OpenBao instance.

	Uses Frappe's RQ job queue with retry support:
	- 3 retry attempts
	- 60 seconds between retries

	Args:
	    remote_name: Name of the remote (from vault_remotes config)
	    path: Secret path (e.g., "frappe/User/admin/password")
	    data: Secret data to write
	"""
	frappe.enqueue(
		"frappe_vault.vault_sync.remote_write_job",
		queue="default",
		timeout=30,
		retry=3,
		retry_in=60,
		remote_name=remote_name,
		path=path,
		data=data,
	)


def enqueue_remote_delete(remote_name: str, path: str) -> None:
	"""
	Enqueue a background job to delete from a remote OpenBao instance.

	Uses Frappe's RQ job queue with retry support:
	- 3 retry attempts
	- 60 seconds between retries

	Args:
	    remote_name: Name of the remote (from vault_remotes config)
	    path: Secret path (e.g., "frappe/User/admin/password")
	"""
	frappe.enqueue(
		"frappe_vault.vault_sync.remote_delete_job",
		queue="default",
		timeout=30,
		retry=3,
		retry_in=60,
		remote_name=remote_name,
		path=path,
	)


def remote_write_job(remote_name: str, path: str, data: dict[str, Any]) -> None:
	"""
	Background job to write a secret to a remote OpenBao instance.

	This function is called by the RQ worker. On failure, RQ handles
	retry according to the retry/retry_in parameters set during enqueue.

	Args:
	    remote_name: Name of the remote (from vault_remotes config)
	    path: Secret path (e.g., "frappe/User/admin/password")
	    data: Secret data to write

	Raises:
	    VaultError: If the write fails (triggers RQ retry)
	"""
	remote_clients = get_remote_clients()
	client = remote_clients.get(remote_name)

	if not client:
		frappe.log_error(
			f"Remote '{remote_name}' not found in vault_remotes config",
			"Vault Sync Error",
		)
		return  # Don't retry if config is wrong

	try:
		client.set_secret_raw(path, data)
	except VaultError as e:
		frappe.log_error(
			f"Failed to write to remote '{remote_name}': {e}",
			"Vault Sync Error",
		)
		raise  # Re-raise to trigger RQ retry


def remote_delete_job(remote_name: str, path: str) -> None:
	"""
	Background job to delete a secret from a remote OpenBao instance.

	Args:
	    remote_name: Name of the remote (from vault_remotes config)
	    path: Secret path (e.g., "frappe/User/admin/password")

	Raises:
	    VaultError: If the delete fails (triggers RQ retry)
	"""
	remote_clients = get_remote_clients()
	client = remote_clients.get(remote_name)

	if not client:
		frappe.log_error(
			f"Remote '{remote_name}' not found in vault_remotes config",
			"Vault Sync Error",
		)
		return  # Don't retry if config is wrong

	try:
		api_path = f"/v1/secret/metadata/{path}"
		response = client._make_request("DELETE", api_path)
		if response.status_code not in (200, 204, 404):
			raise VaultError(f"Failed to delete: {response.status_code}")
	except VaultError as e:
		frappe.log_error(
			f"Failed to delete from remote '{remote_name}': {e}",
			"Vault Sync Error",
		)
		raise  # Re-raise to trigger RQ retry


def reconcile_all() -> dict[str, Any]:
	"""
	Scheduled job to reconcile secrets between local and all remote OpenBao instances.

	This function:
	1. Lists all secrets from local and each remote
	2. Compares metadata timestamps
	3. Syncs newer secrets in both directions (bidirectional)

	Uses last-write-wins conflict resolution based on KV v2 `updated_time`.

	Returns:
	    Summary dict with sync statistics
	"""
	if not is_sync_enabled():
		return {"status": "skipped", "reason": "sync not enabled"}

	local_client = get_vault_client()
	remote_clients = get_remote_clients()

	if not remote_clients:
		return {"status": "skipped", "reason": "no remotes configured"}

	summary = {
		"status": "completed",
		"started_at": now_datetime().isoformat(),
		"remotes": {},
	}

	for remote_name, remote_client in remote_clients.items():
		try:
			result = reconcile_with_remote(local_client, remote_client, remote_name)
			summary["remotes"][remote_name] = result
		except VaultError as e:
			summary["remotes"][remote_name] = {
				"status": "error",
				"error": str(e),
			}
			frappe.log_error(
				f"Reconciliation failed for remote '{remote_name}': {e}",
				"Vault Reconciliation Error",
			)

	summary["completed_at"] = now_datetime().isoformat()
	return summary


def reconcile_with_remote(
	local_client: VaultClient,
	remote_client: VaultClient,
	remote_name: str,
) -> dict[str, Any]:
	"""
	Reconcile secrets between local and a single remote OpenBao instance.

	Args:
	    local_client: Local VaultClient instance
	    remote_client: Remote VaultClient instance
	    remote_name: Name of the remote for logging

	Returns:
	    Dict with sync statistics (pulled, pushed, skipped, errors)
	"""
	stats = {
		"status": "completed",
		"pulled": 0,  # Secrets copied from remote to local
		"pushed": 0,  # Secrets copied from local to remote
		"skipped": 0,  # Secrets already in sync
		"errors": 0,
	}

	# Get all secret paths from both sides
	try:
		local_paths = set(local_client.list_secrets_recursive("frappe"))
	except VaultError:
		local_paths = set()

	try:
		remote_paths = set(remote_client.list_secrets_recursive("frappe"))
	except VaultConnectionError:
		stats["status"] = "error"
		stats["error"] = "Remote unreachable"
		return stats
	except VaultError:
		remote_paths = set()

	# Union of all paths
	all_paths = local_paths | remote_paths

	for path in all_paths:
		try:
			result = compare_and_sync(local_client, remote_client, path)
			if result == "pulled":
				stats["pulled"] += 1
			elif result == "pushed":
				stats["pushed"] += 1
			else:
				stats["skipped"] += 1
		except VaultError as e:
			stats["errors"] += 1
			frappe.log_error(
				f"Error syncing path '{path}' with remote '{remote_name}': {e}",
				"Vault Sync Error",
			)

	return stats


def compare_and_sync(
	local_client: VaultClient,
	remote_client: VaultClient,
	path: str,
) -> str:
	"""
	Compare a single secret between local and remote, sync if needed.

	Uses last-write-wins based on KV v2 metadata `updated_time`.

	Args:
	    local_client: Local VaultClient instance
	    remote_client: Remote VaultClient instance
	    path: Secret path to compare

	Returns:
	    "pulled" if copied from remote to local
	    "pushed" if copied from local to remote
	    "skipped" if already in sync or both missing
	"""
	local_meta = local_client.get_secret_metadata(path)
	remote_meta = remote_client.get_secret_metadata(path)

	# Parse timestamps
	local_time = _parse_timestamp(local_meta.get("updated_time")) if local_meta else None
	remote_time = _parse_timestamp(remote_meta.get("updated_time")) if remote_meta else None

	# Both missing - nothing to do
	if local_time is None and remote_time is None:
		return "skipped"

	# Only exists on one side - copy to the other
	if local_time is None and remote_time is not None:
		# Pull from remote
		secret_data = remote_client.get_secret_with_metadata(path)
		if secret_data and secret_data.get("data"):
			local_client.set_secret_raw(path, secret_data["data"])
		return "pulled"

	if remote_time is None and local_time is not None:
		# Push to remote
		secret_data = local_client.get_secret_with_metadata(path)
		if secret_data and secret_data.get("data"):
			remote_client.set_secret_raw(path, secret_data["data"])
		return "pushed"

	# Both exist - compare timestamps (last-write-wins)
	if remote_time > local_time:
		# Remote is newer, pull
		secret_data = remote_client.get_secret_with_metadata(path)
		if secret_data and secret_data.get("data"):
			local_client.set_secret_raw(path, secret_data["data"])
		return "pulled"

	if local_time > remote_time:
		# Local is newer, push
		secret_data = local_client.get_secret_with_metadata(path)
		if secret_data and secret_data.get("data"):
			remote_client.set_secret_raw(path, secret_data["data"])
		return "pushed"

	# Timestamps are equal - already in sync
	return "skipped"


def _parse_timestamp(ts: str | None) -> datetime | None:
	"""
	Parse an OpenBao/Vault timestamp string to datetime.

	OpenBao uses RFC3339 format: "2024-01-15T10:30:00.123456789Z"

	Args:
	    ts: Timestamp string or None

	Returns:
	    datetime object or None
	"""
	if not ts:
		return None

	try:
		# Handle nanosecond precision by truncating to microseconds
		# Format: 2024-01-15T10:30:00.123456789Z
		if "." in ts:
			# Split at decimal, keep only 6 digits of fractional seconds
			base, frac = ts.split(".")
			frac = frac.rstrip("Z")[:6]
			ts = f"{base}.{frac}Z"

		# Parse ISO format
		return datetime.fromisoformat(ts.replace("Z", "+00:00"))
	except (ValueError, AttributeError):
		return None
