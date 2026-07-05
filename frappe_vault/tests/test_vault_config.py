# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import frappe

from frappe_vault import is_vault_user_passwords_enabled
from frappe_vault.vault_config import is_frappe_vault_installed, vault_feature_active


def test_is_frappe_vault_installed(monkeypatch):
	monkeypatch.setattr(frappe, "get_installed_apps", lambda **_kw: ["frappe", "cloud_storage"])
	assert is_frappe_vault_installed() is False

	monkeypatch.setattr(frappe, "get_installed_apps", lambda **_kw: ["frappe", "frappe_vault"])
	assert is_frappe_vault_installed() is True


def test_vault_feature_inactive_without_installed_app(monkeypatch):
	monkeypatch.setitem(frappe.conf, "enable_vault_user_passwords", True)
	monkeypatch.setattr(frappe, "get_installed_apps", lambda **_kw: ["frappe", "cloud_storage"])
	frappe.flags.in_install = False

	assert vault_feature_active("enable_vault_user_passwords") is False
	assert is_vault_user_passwords_enabled() is False


def test_vault_feature_inactive_during_site_install(monkeypatch):
	monkeypatch.setitem(frappe.conf, "enable_vault_user_passwords", True)
	monkeypatch.setattr(frappe, "get_installed_apps", lambda **_kw: ["frappe"])
	frappe.flags.in_install_db = True

	assert vault_feature_active("enable_vault_user_passwords") is False
	assert is_vault_user_passwords_enabled() is False

	frappe.flags.in_install_db = False


def test_vault_feature_active_when_app_installed(monkeypatch):
	monkeypatch.setitem(frappe.conf, "enable_vault_user_passwords", True)
	monkeypatch.setattr(frappe, "get_installed_apps", lambda **_kw: ["frappe", "frappe_vault"])
	frappe.flags.in_install = False
	frappe.flags.in_install_db = False

	assert vault_feature_active("enable_vault_user_passwords") is True
	assert is_vault_user_passwords_enabled() is True
