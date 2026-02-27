# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

# To test locally:
#   pytest ./frappe_vault/tests/ui/test_list_view.py --browser chromium --headed --disable-warnings

import json
from pathlib import Path

import pytest
from frappe.utils import get_bench_path
from playwright.sync_api import expect

from frappe_vault.tests.test_utils import get_browser_url


def _navigate_to_list(page):
	"""Navigate to the Vault Secret list view and wait for it to render."""
	page.goto(f"{get_browser_url()}/app/vault-secret")
	page.wait_for_selector(".list-row, .no-result", timeout=10000)


def _get_site_config_path() -> Path:
	sites_path = Path(get_bench_path()) / "sites"
	if (sites_path / "currentsite.txt").is_file():
		site = (sites_path / "currentsite.txt").read_text().strip()
		return sites_path / site / "site_config.json"
	common = sites_path / "common_site_config.json"
	if common.is_file():
		default_site = json.loads(common.read_text()).get("default_site")
		if default_site:
			return sites_path / default_site / "site_config.json"
	configs = list(sites_path.glob("*/site_config.json"))
	if len(configs) == 1:
		return configs[0]
	raise RuntimeError(f"Cannot determine active Frappe site under {sites_path}")


# ---------------------------------------------------------------------------
# Vault-specific list view behaviour
# ---------------------------------------------------------------------------


@pytest.mark.order(20)
def test_list_shows_secrets_when_api_enabled(page):
	"""When vault_secrets_api_enabled is true, the list view shows Vault Secrets.

	This verifies our permission_query_conditions hook returns '' (not '1=0')
	and that records created in setup.py are accessible.
	"""
	_navigate_to_list(page)
	# At least one row must be visible — confirms the permission gate is open
	expect(page.locator(".list-row").first).to_be_visible()


@pytest.mark.order(21)
def test_list_disabled_shows_message_when_api_off(page):
	"""When vault_secrets_api_enabled is false the list shows our custom message
	and hides the Create button.

	This tests the vault_secret_list.js onload handler which calls
	vault_proxy.status and conditionally disables the UI.
	"""
	config_path = _get_site_config_path()
	config = json.loads(config_path.read_text())
	original = config.get("vault_secrets_api_enabled")

	try:
		# Land on the list first so reload() stays on the right page
		_navigate_to_list(page)

		config["vault_secrets_api_enabled"] = False
		config_path.write_text(json.dumps(config, indent=1))

		# Hard reload — the server picks up the new config on every fresh request,
		# so the page initialises with secrets_api_enabled=false from the start.
		page.reload()
		page.wait_for_selector(".list-row, .no-result", timeout=10000)

		# Our custom empty-state message must be visible (set by the refresh hook)
		expect(page.get_by_text("Secrets UI must be enabled in the site config.")).to_be_visible(
			timeout=8000
		)

		# Frappe renders the primary action as "Add Vault Secret"; with can_create=false
		# set_primary_action() calls page.clear_primary_action() which removes the button
		expect(page.get_by_role("button", name="Add Vault Secret")).not_to_be_visible()

	finally:
		if original is None:
			config.pop("vault_secrets_api_enabled", None)
		else:
			config["vault_secrets_api_enabled"] = original
		config_path.write_text(json.dumps(config, indent=1))
