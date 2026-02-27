# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

from contextlib import contextmanager
from urllib.parse import urlparse, urlunparse

import frappe


@contextmanager
def use_current_db_transaction():
	"""Refresh the database transaction scope.

	Playwright tests run in a browser process that commits data independently
	from the pytest process's open transaction. Wrapping DB assertions in this
	context manager forces the pytest process to see data committed by browser
	actions (form saves, API calls, etc.).
	"""
	frappe.db.rollback()
	frappe.db.begin()
	yield


def get_browser_url() -> str:
	"""Return the bench URL with the hostname replaced by localhost.

	frappe.utils.get_url() returns the configured site hostname (e.g.
	http://uhdei:8004), which the Playwright browser process cannot resolve
	in WSL2 or other environments where the hostname isn't in DNS/hosts.
	Replacing the host with localhost while preserving the port keeps the
	URL valid for both the pytest process and the headless browser.
	"""
	parsed = urlparse(frappe.utils.get_url())
	netloc = f"localhost:{parsed.port}" if parsed.port else "localhost"
	return urlunparse(parsed._replace(netloc=netloc))
