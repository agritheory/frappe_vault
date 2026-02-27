# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

# To test locally:
#   activate the virtual environment, then bench start, then run:
#   pytest ./frappe_vault/tests/ui/ --browser chromium --headed --disable-warnings
#
# To run UI tests in parallel with unit/integration tests:
#   pytest -n auto --dist=loadfile --browser chromium --disable-warnings
#   (-n auto uses all CPU cores; --dist=loadfile keeps each file on one worker
#    so pytest-order decorators are respected within each file)
#
# Prerequisites:
#   Test data must exist (run `bench run-tests --app frappe_vault` once to seed it via setup.py)
#   vault_secrets_api_enabled is managed automatically by the enable_vault_secrets_api fixture below

import json
from pathlib import Path
from unittest.mock import MagicMock

import frappe
import pytest
from frappe.utils import get_bench_path

from frappe_vault.tests.test_utils import get_browser_url


def _get_logger(*args, **kwargs):
	from frappe.utils.logger import get_logger

	return get_logger(
		module=None,
		with_more_info=False,
		allow_site=True,
		filter=None,
		max_size=100_000,
		file_count=20,
		stream_only=True,
	)


def _get_site_config_path() -> Path:
	"""Resolve the site_config.json path for the active site.

	Resolution order:
	1. currentsite.txt (set by bench run-tests)
	2. common_site_config.json default_site (the normal dev site)
	3. Any single site_config.json found under sites/
	"""
	sites_path = Path(get_bench_path()) / "sites"

	if (sites_path / "currentsite.txt").is_file():
		site = (sites_path / "currentsite.txt").read_text().strip()
		return sites_path / site / "site_config.json"

	common = sites_path / "common_site_config.json"
	if common.is_file():
		default_site = json.loads(common.read_text()).get("default_site")
		if default_site:
			return sites_path / default_site / "site_config.json"

	# Last resort: find the only site_config.json present
	configs = list(sites_path.glob("*/site_config.json"))
	if len(configs) == 1:
		return configs[0]

	raise RuntimeError(f"Cannot determine active Frappe site under {sites_path}")


@pytest.fixture(scope="session", autouse=True)
def db_instance():
	frappe.logger = _get_logger

	sites = Path(get_bench_path()) / "sites"
	currentsite = "test_site"
	if (sites / "common_site_config.json").is_file():
		currentsite = json.loads((sites / "common_site_config.json").read_text()).get("default_site")

	frappe.init(site=currentsite, sites_path=sites)
	frappe.connect()
	frappe.db.commit = MagicMock()

	# Ensure vault secrets API is enabled for the test session by writing directly
	# to site_config.json. The running Frappe server reads this file on every
	# request, so this is the only reliable way to affect the server process.
	config_path = _get_site_config_path()
	config = json.loads(config_path.read_text())
	original_value = config.get("vault_secrets_api_enabled")
	if not original_value:
		config["vault_secrets_api_enabled"] = True
		config_path.write_text(json.dumps(config, indent=1))

	yield frappe.db

	# Restore site config
	config = json.loads(config_path.read_text())
	if original_value is None:
		config.pop("vault_secrets_api_enabled", None)
	else:
		config["vault_secrets_api_enabled"] = original_value
	config_path.write_text(json.dumps(config, indent=1))


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
	return {
		**browser_context_args,
		"viewport": {
			"width": 1280,
			"height": 900,
		},
	}


@pytest.fixture(autouse=True)
def setup(page):
	page.set_default_timeout(8000)

	page.goto(get_browser_url())

	# Frappe redirects unauthenticated visits to /login
	page.get_by_role("textbox", name="Email").fill("Administrator")
	page.get_by_role("textbox", name="Password").fill("admin")
	page.get_by_role("button", name="Login").click()
	page.wait_for_url("**/app**")

	# Frappe persists list view filters in Redis and merges (not replaces) on save,
	# so passing {} to the built-in save endpoint is a no-op. Our custom endpoint
	# calls update_user_settings(for_update=True) which does a full replacement.
	# frappe.xcall returns a native Promise so Playwright awaits it correctly.
	page.evaluate(
		"""async () => {
		await frappe.xcall('frappe_vault.vault_proxy.reset_list_user_settings', {
			doctype: 'Vault Secret'
		});
		frappe.model.user_settings['Vault Secret'] = {};
	}"""
	)

	yield

	# Clean up any Vault Secrets created during the test
	_delete_ui_test_secrets()


def _delete_ui_test_secrets():
	"""Remove secrets created by UI tests (identified by 'UI Test' title prefix)."""
	test_secrets = frappe.get_all(
		"Vault Secret",
		filters={"title": ["like", "UI Test%"]},
		pluck="name",
		ignore_permissions=True,
	)
	for name in test_secrets:
		frappe.delete_doc("Vault Secret", name, force=True, ignore_permissions=True)
