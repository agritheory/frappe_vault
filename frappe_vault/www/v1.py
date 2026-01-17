# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

"""
Vault API-compatible route handler.

This page controller handles requests to /v1/* and proxies them to OpenBao,
providing API compatibility with Vault/OpenBao clients.
"""

import json

import frappe
from frappe import _

from frappe_vault.vault_client import VaultError, get_vault_client
from frappe_vault.vault_proxy import (
	get_vault_allowed_roles,
	has_vault_access,
	is_vault_proxy_enabled,
	log_vault_access,
)

no_cache = 1


def get_context(context):
	"""Handle the Vault API request and return JSON response."""
	# Set response type to JSON
	frappe.response["type"] = "json"

	# Check if proxy is enabled
	if not is_vault_proxy_enabled():
		frappe.response["http_status_code"] = 503
		frappe.response["message"] = {"errors": ["Vault proxy is not enabled"]}
		return

	# Check authentication - allow both session auth and token auth
	if frappe.session.user == "Guest":
		frappe.response["http_status_code"] = 401
		frappe.response["message"] = {"errors": ["Authentication required"]}
		return

	# Check authorization
	if not has_vault_access():
		frappe.response["http_status_code"] = 403
		frappe.response["message"] = {"errors": ["Permission denied"]}
		return

	# Get the full path from the route
	# frappe.form_dict.vault_path contains everything after /v1/
	vault_path = frappe.form_dict.get("vault_path", "")
	full_path = f"/v1/{vault_path}"

	# Get HTTP method
	method = frappe.request.method

	# Block sensitive endpoints
	blocked_paths = [
		"/v1/sys/seal",
		"/v1/sys/unseal",
		"/v1/sys/init",
		"/v1/sys/rekey",
		"/v1/sys/rotate",
		"/v1/auth/token/create",
		"/v1/auth/token/revoke",
	]
	for blocked in blocked_paths:
		if full_path.startswith(blocked):
			frappe.response["http_status_code"] = 403
			frappe.response["message"] = {"errors": [f"Access to {blocked} is not allowed"]}
			log_vault_access("blocked_request", full_path, False, f"method={method}")
			return

	# Get request body for POST/PUT
	request_data = None
	if method in ("POST", "PUT", "PATCH"):
		try:
			if frappe.request.data:
				request_data = json.loads(frappe.request.data)
		except json.JSONDecodeError:
			frappe.response["http_status_code"] = 400
			frappe.response["message"] = {"errors": ["Invalid JSON in request body"]}
			return

	# Proxy the request to OpenBao
	try:
		client = get_vault_client()
		response = client._make_request(method, full_path, data=request_data)

		# Log the access
		log_vault_access("api_request", full_path, True, f"method={method}")

		# Set the response status code
		frappe.response["http_status_code"] = response.status_code

		# Return the response body
		if response.status_code == 204:
			frappe.response["message"] = None
		else:
			try:
				frappe.response["message"] = response.json()
			except json.JSONDecodeError:
				frappe.response["message"] = response.text

	except VaultError as e:
		log_vault_access("api_request", full_path, False, str(e))
		frappe.response["http_status_code"] = 502
		frappe.response["message"] = {"errors": [str(e)]}
