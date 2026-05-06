# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

__version__ = "16.0.0"

import frappe
import frappe.utils.password
from frappe import _
from frappe.sessions import clear_sessions
from frappe.utils.password import delete_login_failed_cache, passlibctx

from frappe_vault.vault_client import VaultClient, VaultError, get_vault_client
from frappe_vault.vault_sync import sync_delete, sync_write

# Store original functions
original_get_decrypted_password = frappe.utils.password.get_decrypted_password
original_set_encrypted_password = frappe.utils.password.set_encrypted_password
original_update_password = frappe.utils.password.update_password
original_check_password = frappe.utils.password.check_password


def is_vault_enabled() -> bool:
	"""Check if Vault secrets are enabled in site config (for encrypted Password fields)."""
	return bool(frappe.conf.get("vault_password_fields_enabled"))


def is_vault_user_passwords_enabled() -> bool:
	"""Check if Vault is enabled for user login passwords (hashed passwords)."""
	return bool(frappe.conf.get("enable_vault_user_passwords"))


def is_field_vault_enabled(doctype: str, fieldname: str) -> bool:
	"""
	Check if a specific field should use Vault storage.

	Returns True if vault is enabled globally and the field is a Password type.

	Args:
	    doctype: The DocType name
	    fieldname: The field name

	Returns:
	    True if the field should use Vault, False otherwise
	"""
	if not is_vault_enabled():
		return False

	try:
		meta = frappe.get_meta(doctype)
		field = meta.get_field(fieldname)

		if not field:
			return False

		return field.fieldtype == "Password"

	except Exception:
		# If we can't get meta (e.g., during installation), fall back to original
		return False


def patched_get_decrypted_password(
	doctype: str,
	name: str,
	fieldname: str = "password",
	raise_exception: bool = True,
) -> str | None:
	"""
	Vault-aware replacement for frappe.utils.password.get_decrypted_password.

	APP: frappe_vault HASH: 59a92b53ac456f5dce802cee7f261d03c2a05df0 REPO: https://github.com/frappe/frappe PATH: frappe/utils/password.py METHOD: get_decrypted_password

	If Vault is enabled and the field is vault-enabled, fetch from Vault.
	Otherwise, use the original Frappe password storage.

	Args:
	    doctype: DocType of the document
	    name: Document name
	    fieldname: Password field name
	    raise_exception: Whether to raise exception if password not found

	Returns:
	    The decrypted password, or None if not found

	Raises:
	    frappe.AuthenticationError: If Vault is unavailable (when raise_exception=True)
	"""
	if not is_field_vault_enabled(doctype, fieldname):
		return original_get_decrypted_password(doctype, name, fieldname, raise_exception)

	try:
		client = get_vault_client()
		secret = client.get_secret(doctype, name, fieldname)

		if secret is None and raise_exception:
			frappe.throw(
				f"Password not found for {doctype} {name} {fieldname}",
				frappe.AuthenticationError,
			)

		return secret

	except VaultError as e:
		frappe.log_error(
			title="Vault Error",
			message=f"Failed to retrieve secret for {doctype}.{name}.{fieldname}: {e}",
		)

		if raise_exception:
			frappe.throw(
				"Secret management service unavailable",
				frappe.AuthenticationError,
			)

		return None


def patched_set_encrypted_password(
	doctype: str,
	name: str,
	pwd: str,
	fieldname: str = "password",
) -> None:
	"""
	Vault-aware replacement for frappe.utils.password.set_encrypted_password.

	APP: frappe_vault HASH: 59a92b53ac456f5dce802cee7f261d03c2a05df0 REPO: https://github.com/frappe/frappe PATH: frappe/utils/password.py METHOD: set_encrypted_password

	If Vault is enabled and the field is vault-enabled, store in Vault and
	propagate to remotes via sync_write when replication is enabled.
	Otherwise, use the original Frappe password storage.

	Args:
	    doctype: DocType of the document
	    name: Document name
	    pwd: The password to store
	    fieldname: Password field name

	Raises:
	    frappe.ValidationError: If Vault is unavailable
	"""
	if not is_field_vault_enabled(doctype, fieldname):
		return original_set_encrypted_password(doctype, name, pwd, fieldname)

	try:
		sync_write(doctype, name, fieldname, pwd)

	except VaultError as e:
		frappe.log_error(
			title="Vault Error",
			message=f"Failed to store secret for {doctype}.{name}.{fieldname}: {e}",
		)
		frappe.throw(
			"Secret management service unavailable",
			frappe.ValidationError,
		)


def patched_delete_password(doctype: str, name: str, fieldname: str = "password") -> None:
	"""
	Delete a password from Vault (if vault-enabled) and/or from Frappe's __Auth table.

	Args:
	    doctype: DocType of the document
	    name: Document name
	    fieldname: Password field name
	"""
	if is_field_vault_enabled(doctype, fieldname):
		try:
			sync_delete(doctype, name, fieldname)
		except VaultError as e:
			frappe.log_error(
				title="Vault Error",
				message=f"Failed to delete secret for {doctype}.{name}.{fieldname}: {e}",
			)

	# Also delete from __Auth table (for cleanup/migration scenarios)
	try:
		frappe.db.delete(
			"__Auth",
			{
				"doctype": doctype,
				"name": name,
				"fieldname": fieldname,
			},
		)
	except Exception:
		pass  # Ignore if not found


def patched_update_password(
	user: str,
	pwd: str,
	doctype: str = "User",
	fieldname: str = "password",
	logout_all_sessions: bool = False,
) -> None:
	"""
	Vault-aware replacement for frappe.utils.password.update_password.

	APP: frappe_vault HASH: 59a92b53ac456f5dce802cee7f261d03c2a05df0 REPO: https://github.com/frappe/frappe PATH: frappe/utils/password.py METHOD: update_password

	If Vault user passwords are enabled, store the hashed password in Vault and
	propagate to remotes via sync_write when replication is enabled.
	Otherwise, use the original Frappe password storage.

	Args:
	    user: Username
	    pwd: The plaintext password to hash and store
	    doctype: DocType name (default: User)
	    fieldname: Field name (default: password)
	    logout_all_sessions: Whether to logout all other sessions
	"""
	if not is_vault_user_passwords_enabled():
		return original_update_password(user, pwd, doctype, fieldname, logout_all_sessions)

	try:
		hashed_pwd = passlibctx.hash(pwd)
		sync_write(doctype, user, fieldname, hashed_pwd)

		# Remove any existing password from __Auth table (no plaintext/hash in DB)
		frappe.db.delete(
			"__Auth",
			{
				"doctype": doctype,
				"name": user,
				"fieldname": fieldname,
			},
		)

		if logout_all_sessions:
			clear_sessions(user=user, force=True)

	except VaultError as e:
		frappe.log_error(
			title="Vault Error",
			message=f"Failed to store user password for {doctype}.{user}.{fieldname}: {e}",
		)
		frappe.throw(
			_("Secret management service unavailable"),
			frappe.ValidationError,
		)


def patched_check_password(
	user: str,
	pwd: str,
	doctype: str = "User",
	fieldname: str = "password",
	delete_tracker_cache: bool = True,
) -> str:
	"""
	Vault-aware replacement for frappe.utils.password.check_password.

	APP: frappe_vault HASH: 59a92b53ac456f5dce802cee7f261d03c2a05df0 REPO: https://github.com/frappe/frappe PATH: frappe/utils/password.py METHOD: check_password

	If Vault user passwords are enabled, verify against the hash stored in Vault.
	Reads always go to local Vault only — remotes are write targets, not read sources.
	Otherwise, use the original Frappe password verification.

	Args:
	    user: Username
	    pwd: The plaintext password to verify
	    doctype: DocType name (default: User)
	    fieldname: Field name (default: password)
	    delete_tracker_cache: Whether to delete login tracker cache

	Returns:
	    The username if password is correct

	Raises:
	    frappe.AuthenticationError: If password is incorrect or Vault unavailable
	"""
	if not is_vault_user_passwords_enabled():
		return original_check_password(user, pwd, doctype, fieldname, delete_tracker_cache)

	try:
		client = get_vault_client()
		stored_hash = client.get_secret(doctype, user, fieldname)

		if not stored_hash or not passlibctx.verify(pwd, stored_hash):
			raise frappe.AuthenticationError(_("Incorrect User or Password"))

		if delete_tracker_cache:
			delete_login_failed_cache(user)

		if passlibctx.needs_update(stored_hash):
			patched_update_password(user, pwd, doctype, fieldname)

		return user

	except VaultError as e:
		frappe.log_error(
			title="Vault Error",
			message=f"Failed to verify password for {doctype}.{user}.{fieldname}: {e}",
		)
		raise frappe.AuthenticationError(_("Secret management service unavailable"))


# Apply monkey patches
frappe.utils.password.get_decrypted_password = patched_get_decrypted_password
frappe.utils.password.set_encrypted_password = patched_set_encrypted_password
frappe.utils.password.update_password = patched_update_password
frappe.utils.password.check_password = patched_check_password
