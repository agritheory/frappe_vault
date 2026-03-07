# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import frappe
import pytest
from frappe.utils import get_bench_path


def get_logger(*args, **kwargs):
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


@pytest.fixture(scope="module")
def monkeymodule():
	with pytest.MonkeyPatch.context() as mp:
		yield mp


@pytest.fixture(scope="session", autouse=True)
def db_instance():
	frappe.logger = get_logger

	sites = Path(get_bench_path()) / "sites"
	currentsite = "test_site"
	if (sites / "currentsite.txt").is_file():
		currentsite = (sites / "currentsite.txt").read_text().strip()
	else:
		common_config = sites / "common_site_config.json"
		if common_config.is_file():
			config = json.loads(common_config.read_text())
			currentsite = config.get("default_site", currentsite)

	frappe.init(site=currentsite, sites_path=sites)
	frappe.connect()
	frappe.db.commit = MagicMock()
	yield frappe.db


@pytest.fixture(scope="module", autouse=True)
def patch_vault_conf(monkeymodule):
	"""Ensure vault secrets and proxy are enabled for tests.

	frappe.conf is a frappe._dict (dict subclass), so setitem must be used —
	setattr only sets instance attributes, which conf.get() never sees.

	vault_url and vault_token are taken from site config by default.
	Set BAO_ADDR/BAO_TOKEN (or VAULT_ADDR/VAULT_TOKEN) env vars to override.
	"""
	monkeymodule.setitem(frappe.conf, "vault_secrets_api_enabled", True)
	monkeymodule.setitem(frappe.conf, "enable_vault_user_passwords", True)
	monkeymodule.setitem(frappe.conf, "vault_proxy_enabled", True)

	url_override = os.environ.get("BAO_ADDR") or os.environ.get("VAULT_ADDR")
	if url_override:
		monkeymodule.setitem(frappe.conf, "vault_url", url_override)

	token_override = os.environ.get("BAO_TOKEN") or os.environ.get("VAULT_TOKEN")
	if token_override:
		monkeymodule.setitem(frappe.conf, "vault_token", token_override)


@pytest.fixture(autouse=True)
def reset_user():
	"""Restore Administrator session after each test to prevent user context leaking."""
	yield
	frappe.set_user("Administrator")
