# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import click
import frappe
from frappe.commands import get_site, pass_context


@click.command("migrate-passwords-to-vault")
@click.option("--dry-run", is_flag=True, help="Show what would be migrated without making changes")
@click.option("--skip-backup", is_flag=True, help="Skip creating a backup (not recommended)")
@pass_context
def migrate_passwords_to_vault(context, dry_run=False, skip_backup=False):
	"""Migrate existing passwords from __Auth table to OpenBao."""
	site = get_site(context)
	frappe.init(site=site)
	frappe.connect()

	from frappe_vault.install import migrate_passwords_to_vault as do_migrate

	try:
		do_migrate(dry_run=dry_run, skip_backup=skip_backup)
	finally:
		frappe.destroy()


@click.command("restore-auth-backup")
@click.argument("backup_file")
@pass_context
def restore_auth_backup(context, backup_file):
	"""Restore __Auth table from a backup file created during migration."""
	site = get_site(context)
	frappe.init(site=site)
	frappe.connect()

	from frappe_vault.install import restore_auth_backup as do_restore

	try:
		do_restore(backup_file)
	finally:
		frappe.destroy()


@click.command("cleanup-migrated-passwords")
@click.option("--confirm", is_flag=True, help="Actually delete the migrated passwords from __Auth")
@pass_context
def cleanup_migrated_passwords(context, confirm=False):
	"""Remove passwords from __Auth that have been migrated to OpenBao."""
	site = get_site(context)
	frappe.init(site=site)
	frappe.connect()

	from frappe_vault.install import cleanup_migrated_passwords as do_cleanup

	try:
		do_cleanup(confirm=confirm)
	finally:
		frappe.destroy()


@click.command("vault-status")
@pass_context
def vault_status(context):
	"""Check OpenBao connectivity and configuration status."""
	site = get_site(context)
	frappe.init(site=site)
	frappe.connect()

	try:
		print(f"Site: {site}")
		print(f"enable_vault_secrets: {frappe.conf.get('enable_vault_secrets', False)}")
		print(f"enable_vault_user_passwords: {frappe.conf.get('enable_vault_user_passwords', False)}")
		print(f"vault_url: {frappe.conf.get('vault_url', 'not set')}")
		print(f"vault_proxy_enabled: {frappe.conf.get('vault_proxy_enabled', False)}")

		from frappe_vault.vault_client import get_vault_client

		try:
			client = get_vault_client()
			available = client.is_available()
			print(f"\nOpenBao Status: {'✓ Connected' if available else '✗ Not Available'}")
			if available:
				health = client.check_health()
				print(f"  Initialized: {health.get('initialized', 'unknown')}")
				print(f"  Sealed: {health.get('sealed', 'unknown')}")
		except Exception as e:
			print(f"\nOpenBao Status: ✗ Error - {e}")

		# Count passwords in __Auth
		Auth = frappe.qb.Table("__Auth")
		auth_count = (
			frappe.qb.from_(Auth)
			.select(frappe.qb.functions.Count("*"))
			.where(Auth.password.isnotnull())
			.run()
		)[0][0]
		print(f"\nPasswords in __Auth table: {auth_count}")

	finally:
		frappe.destroy()


commands = [
	migrate_passwords_to_vault,
	cleanup_migrated_passwords,
	restore_auth_backup,
	vault_status,
]
