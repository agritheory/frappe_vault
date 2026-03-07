# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import frappe

VAULT_HEALTH_CACHE_TTL = 30  # seconds


def any_vault_feature_enabled() -> bool:
	return bool(
		frappe.conf.get("enable_vault_secrets") or frappe.conf.get("enable_vault_user_passwords")
	)


def vault_available_cached() -> bool:
	"""
	Return OpenBao availability, cached per site for VAULT_HEALTH_CACHE_TTL seconds.

	Avoids a live HTTP round-trip on every login page load while still
	recovering automatically once OpenBao comes back online.
	"""
	cache_key = f"vault_health:{frappe.local.site}"
	cached = frappe.cache.get_value(cache_key)
	if cached is not None:
		return cached

	from frappe_vault.vault_client import get_vault_client

	available = get_vault_client().is_available()
	frappe.cache.set_value(cache_key, available, expires_in_sec=VAULT_HEALTH_CACHE_TTL)
	return available


def before_request() -> None:
	"""
	Redirect the login page to a maintenance screen when OpenBao is unreachable.

	Only activates when:
	  - the request is a GET to /login
	  - at least one vault feature (secrets or user passwords) is enabled
	  - OpenBao is not responding (result cached for VAULT_HEALTH_CACHE_TTL seconds)

	POST requests to /api/method/login are intentionally left alone — they will
	raise frappe.AuthenticationError via the patched check_password, which is the
	correct behaviour for programmatic callers.
	"""
	if frappe.request.method != "GET":
		return

	if frappe.local.path != "login":
		return

	if not any_vault_feature_enabled():
		return

	if not vault_available_cached():
		frappe.redirect("/vault-unavailable")
