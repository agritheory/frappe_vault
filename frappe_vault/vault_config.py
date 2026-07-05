# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import frappe


def is_frappe_vault_installed() -> bool:
	"""Return True when frappe_vault is installed on the current site."""
	if getattr(frappe.flags, "in_install_db", False):
		return False

	if getattr(frappe.local, "site", None) and frappe.db:
		try:
			return "frappe_vault" in frappe.get_installed_apps()
		except Exception:
			pass

	configured_apps = frappe.conf.get("installed_apps")
	if isinstance(configured_apps, list):
		return "frappe_vault" in configured_apps

	return False


def vault_feature_active(conf_flag: str) -> bool:
	"""Return True when a vault config flag is set and frappe_vault is installed on the site."""
	return bool(frappe.conf.get(conf_flag)) and is_frappe_vault_installed()
