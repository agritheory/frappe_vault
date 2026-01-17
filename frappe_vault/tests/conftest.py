# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import os
from pathlib import Path
from unittest.mock import MagicMock

import frappe
import pytest
from frappe.utils import get_bench_path


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


@pytest.fixture(scope="module")
def monkeymodule():
	with pytest.MonkeyPatch.context() as mp:
		yield mp


@pytest.fixture(scope="session", autouse=True)
def db_instance():
	frappe.logger = _get_logger

	currentsite = "test_site"
	sites = Path(get_bench_path()) / "sites"
	if (sites / "currentsite.txt").is_file():
		currentsite = (sites / "currentsite.txt").read_text().strip()

	frappe.init(site=currentsite, sites_path=sites)
	frappe.connect()
	frappe.db.commit = MagicMock()
	yield frappe.db


@pytest.fixture(scope="module", autouse=True)
def patch_vault_conf(monkeymodule):
	"""Patch frappe.conf with OpenBao test settings from environment.

	Supports both BAO_* and VAULT_* environment variables for backward compatibility.
	BAO_* variables take precedence.
	"""
	monkeymodule.setattr(
		"frappe.conf.enable_vault_secrets",
		True,
	)
	monkeymodule.setattr(
		"frappe.conf.enable_vault_user_passwords",
		True,
	)
	monkeymodule.setattr(
		"frappe.conf.vault_url",
		os.environ.get("BAO_ADDR") or os.environ.get("VAULT_ADDR", "http://localhost:8200"),
	)
	monkeymodule.setattr(
		"frappe.conf.vault_token",
		os.environ.get("BAO_TOKEN") or os.environ.get("VAULT_TOKEN", "test-token"),
	)
	monkeymodule.setattr(
		"frappe.conf.vault_verify_ssl",
		False,
	)
