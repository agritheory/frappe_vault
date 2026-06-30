# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from datetime import datetime
from getpass import getpass
from sys import platform

import frappe

from frappe_vault.vault_client import VaultError, get_vault_client


def is_root():
	return os.geteuid() == 0


def can_install_openbao_locally():
	"""Return True only when this environment can realistically install OpenBao.

	Frappe Cloud and other containerised platforms run as an unprivileged user
	without sudo, apt, or write access to system directories. In those cases we
	skip the local install and expect an external OpenBao instance to be
	configured via site_config (vault_url / vault_token) or env vars.
	"""
	if platform != "linux":
		return False
	if is_root():
		return True
	if not sys.stdin.isatty():
		return False
	return test_sudo()


def test_sudo():
	args = "sudo -S echo OK".split()
	kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
	try:
		cmd = subprocess.run(args, **kwargs)
		return "OK" in (cmd.stdout or "")
	except (FileNotFoundError, OSError):
		return False


def install_package(module, pwd=""):
	args = f"sudo -S apt-get -y install {module}".split()
	kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
	if pwd:
		kwargs.update(input=pwd)
	cmd = subprocess.run(args, **kwargs)
	return cmd.stdout, cmd.stderr


def check_openbao_installed():
	"""Check if bao command is available OR OpenBao server is reachable via HTTP.

	The HTTP check covers CI environments where OpenBao runs as a Docker
	service on localhost but the bao binary is not installed.
	"""
	if shutil.which("bao") is not None:
		return True
	try:
		vault_url = frappe.conf.get("vault_url", "http://localhost:8200")
		try:
			with urllib.request.urlopen(f"{vault_url}/v1/sys/health", timeout=2) as r:
				return r.status in (200, 429, 472, 473, 501, 503)
		except urllib.error.HTTPError as e:
			# urllib raises HTTPError for non-2xx — 501 (not initialized) and
			# 503 (sealed) both mean OpenBao is running.
			return e.code in (200, 429, 472, 473, 501, 503)
	except Exception:
		return False


def install_openbao():
	"""Install OpenBao if not present.

	OpenBao is an open-source fork of HashiCorp Vault (MPL-2.0 licensed)
	governed by the Open Source Security Foundation (OpenSSF).
	See: https://openbao.org

	Tries apt first (if the repo is reachable), then falls back to downloading
	the binary directly from GitHub releases.
	"""
	if check_openbao_installed():
		print("OpenBao is already installed.")
		return

	if not can_install_openbao_locally():
		print(
			"Cannot install OpenBao locally (no root/sudo or non-interactive environment). "
			"If you are on Frappe Cloud, configure an external OpenBao instance via "
			"site_config.json (vault_url / vault_token) or environment variables "
			"(BAO_ADDR / BAO_TOKEN)."
		)
		return

	pwd = ""
	if not is_root():
		pwd = getpass("Provide sudo password to install OpenBao: ")

	# --- Try apt first ---------------------------------------------------
	apt_ok = False
	try:
		print("Trying apt install...")
		commands = [
			"sudo -S apt-get update",
			"sudo -S apt-get install -y gpg coreutils wget",
			"wget -O- https://apt.releases.openbao.org/gpg | sudo gpg --dearmor -o /usr/share/keyrings/openbao-archive-keyring.gpg",
			'echo "deb [signed-by=/usr/share/keyrings/openbao-archive-keyring.gpg] https://apt.releases.openbao.org $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/openbao.list',
			"sudo -S apt-get update",
		]
		for cmd in commands:
			kwargs = dict(
				shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8"
			)
			if pwd:
				kwargs.update(input=pwd)
			result = subprocess.run(cmd, **kwargs)
			if result.returncode != 0 and "already exists" not in result.stderr:
				print(f"Warning: {result.stderr}")
				break
		else:
			out, err = install_package("openbao", pwd)
			if err and "already" not in err.lower():
				print(f"apt install error: {err}")
			apt_ok = check_openbao_installed()
	except Exception as e:
		print(f"apt install failed: {e}")

	if apt_ok:
		print("OpenBao installed successfully via apt.")
		return

	# --- Fall back to GitHub binary download ----------------------------
	print("apt unavailable; downloading OpenBao binary from GitHub releases...")
	try:
		req = urllib.request.Request(
			"https://api.github.com/repos/openbao/openbao/releases/latest",
			headers={"Accept": "application/vnd.github+json"},
		)
		with urllib.request.urlopen(req, timeout=10) as r:
			release = json.loads(r.read())
		tag = release["tag_name"]
		version = tag.lstrip("v")

		tar_url = None
		tar_name = None
		for asset in release.get("assets", []):
			name = asset["name"]
			name_lower = name.lower()
			if (
				name_lower.endswith(".tar.gz")
				and name_lower.startswith("bao_")
				and "linux" in name_lower
				and ("x86_64" in name_lower or "amd64" in name_lower)
			):
				tar_url = asset["browser_download_url"]
				tar_name = name
				break

		if not tar_url:
			raise RuntimeError(
				f"No Linux x86_64 tar.gz asset in release {tag}. "
				f"Available: {[a['name'] for a in release.get('assets', [])]}"
			)

		print(f"Downloading {tar_name} ...")
		tar_path = f"/tmp/bao_{version}.tar.gz"
		urllib.request.urlretrieve(tar_url, tar_path)

		extract_dir = f"/tmp/bao_{version}_extract"
		os.makedirs(extract_dir, exist_ok=True)
		with tarfile.open(tar_path, "r:gz") as tf:
			binary_member = next(
				(m for m in tf.getmembers() if m.name in ("bao", "./bao") or m.name.endswith("/bao")),
				None,
			)
			if not binary_member:
				raise RuntimeError(f"No bao binary in tarball. Contents: {[m.name for m in tf.getmembers()]}")
			binary_member.name = os.path.basename(binary_member.name)
			tf.extract(binary_member, extract_dir)

		dest = "/usr/local/bin/bao"
		subprocess.run(["sudo", "mv", os.path.join(extract_dir, "bao"), dest], check=True)
		subprocess.run(["sudo", "chmod", "+x", dest], check=True)

		if check_openbao_installed():
			print(f"OpenBao {version} installed to {dest}.")
		else:
			print("Download succeeded but 'bao' still not found — check PATH.")

	except Exception as e:
		print(f"GitHub download failed: {e}")
		print("Please install OpenBao manually: https://openbao.org/docs/install")


def get_user_confirmation():
	"""Prompt for supervisor setup confirmation."""
	while True:
		user_input = (
			input(
				"Frappe Vault requires OpenBao to be managed by Supervisor. "
				"Do you want to run 'bench setup supervisor' to update the config? (yes/no): "
			)
			.strip()
			.lower()
		)
		if user_input in ["yes", "y"]:
			return True
		elif user_input in ["no", "n"]:
			return False
		else:
			print("Please enter 'yes' or 'no'.")


def check_openbao_supervisor_config():
	"""Check if OpenBao is configured in supervisor and prompt for setup if needed."""
	# Skip supervisor setup when we don't have privileges (e.g. Frappe Cloud).
	if not (is_root() or test_sudo()):
		print(
			"No root/sudo access; skipping supervisor configuration. "
			"On Frappe Cloud you must run OpenBao externally."
		)
		return

	# Skip supervisor setup on development setups
	if not (frappe.conf.restart_supervisor_on_update or frappe.conf.restart_systemd_on_update):
		print(
			"Development setup detected. Ensure OpenBao is running locally:\n"
			"  bao server -dev -dev-listen-address=127.0.0.1:8200"
		)
		return

	# Check if supervisor config exists and contains openbao or vault (for backward compat)
	supervisor_conf_path = "/etc/supervisor/conf.d/frappe-bench.conf"
	configured = False

	if os.path.exists(supervisor_conf_path):
		with open(supervisor_conf_path) as f:
			content = f.read().lower()
			if "openbao" in content or "bao" in content:
				configured = True

	if configured:
		print("OpenBao appears to be configured in supervisor.")
		return

	print(
		"OpenBao does not appear to be configured in supervisor.\n"
		"You will need to set up OpenBao for this bench.\n"
		"\n"
		"RECOMMENDED: Use the automated setup:\n"
		"\n"
		"     bench generate-seal-key --init-config\n"
		"\n"
		"This creates OpenBao config files in your bench's config/ directory\n"
		"and provides supervisor configuration instructions.\n"
		"\n"
		"With static seal configured, OpenBao will auto-unseal after bench restart.\n"
		"See docs/openbao-setup.md for complete setup instructions.\n"
	)

	if not get_user_confirmation():
		print("Please configure OpenBao in supervisor manually.")
		return

	process = subprocess.Popen(
		"bench setup supervisor --yes",
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		text=True,
	)
	stdout, stderr = process.communicate()

	if process.returncode != 0:
		if "INFO: A newer version of bench is available" not in stderr:
			print(f"Command failed: {stderr}.")
		else:
			print(f"Command failed: {stdout}.")
	else:
		print(
			"Supervisor config regenerated. You still need to manually add the OpenBao program section."
		)


# Backward compatibility aliases
check_vault_installed = check_openbao_installed
install_vault = install_openbao
check_vault_supervisor_config = check_openbao_supervisor_config


def before_install():
	"""Run before app installation."""
	try:
		install_openbao()
	except Exception as e:
		print(f"Warning: OpenBao installation step failed: {e}")

	try:
		check_openbao_supervisor_config()
	except Exception as e:
		print(f"Warning: Supervisor configuration step failed: {e}")


def after_install():
	"""Run after app installation - migrate existing passwords to OpenBao."""
	migrate_passwords_to_vault()


def backup_auth_table() -> str | None:
	"""
	Create a SQL backup of the __Auth table before migration using mysqldump.

	Returns:
	    Path to the backup file, or None if backup failed
	"""
	# Check if there are any entries to backup
	count = frappe.db.sql("SELECT COUNT(*) FROM `__Auth`")[0][0]

	if not count:
		return None

	# Create backup in site's private files
	backup_dir = frappe.get_site_path("private", "backups")
	os.makedirs(backup_dir, exist_ok=True)

	timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
	backup_file = os.path.join(backup_dir, f"__Auth_backup_{timestamp}.sql")

	# Get database credentials from site config
	db_name = frappe.conf.db_name
	db_user = frappe.conf.db_user or "root"
	db_password = frappe.conf.db_password or ""
	db_host = frappe.conf.db_host or "localhost"
	db_port = frappe.conf.db_port or 3306

	# Build mysqldump command
	cmd = [
		"mysqldump",
		f"--host={db_host}",
		f"--port={db_port}",
		f"--user={db_user}",
		f"--password={db_password}",
		"--single-transaction",
		"--quick",
		db_name,
		"__Auth",
	]

	try:
		with open(backup_file, "w") as f:
			result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, text=True)

		if result.returncode != 0:
			print(f"mysqldump warning: {result.stderr}")
			# Check if file was created despite warning
			if not os.path.exists(backup_file) or os.path.getsize(backup_file) == 0:
				return None

		return backup_file

	except FileNotFoundError:
		print("mysqldump not found. Skipping backup.")
		return None
	except Exception as e:
		print(f"Backup failed: {e}")
		return None


def restore_auth_backup(backup_file: str) -> dict:
	"""
	Restore __Auth table from a SQL backup file.

	Args:
	    backup_file: Path to the backup SQL file

	Returns:
	    Dictionary with restore statistics
	"""
	if not os.path.exists(backup_file):
		print(f"Backup file not found: {backup_file}")
		return {"restored": False, "error": "file_not_found"}

	# Get database credentials from site config
	db_name = frappe.conf.db_name
	db_user = frappe.conf.db_user or "root"
	db_password = frappe.conf.db_password or ""
	db_host = frappe.conf.db_host or "localhost"
	db_port = frappe.conf.db_port or 3306

	# Build mysql command
	cmd = [
		"mysql",
		f"--host={db_host}",
		f"--port={db_port}",
		f"--user={db_user}",
		f"--password={db_password}",
		db_name,
	]

	try:
		with open(backup_file) as f:
			result = subprocess.run(cmd, stdin=f, stderr=subprocess.PIPE, text=True)

		if result.returncode != 0:
			print(f"Restore failed: {result.stderr}")
			return {"restored": False, "error": result.stderr}

		print(f"Successfully restored from {backup_file}")
		return {"restored": True}

	except FileNotFoundError:
		print("mysql client not found.")
		return {"restored": False, "error": "mysql_not_found"}
	except Exception as e:
		print(f"Restore failed: {e}")
		return {"restored": False, "error": str(e)}


def migrate_passwords_to_vault(dry_run: bool = False, skip_backup: bool = False) -> dict:
	"""
	Migrate existing passwords from __Auth table to OpenBao.

	This function reads all passwords stored in Frappe's __Auth table
	and copies them to OpenBao. The original entries are preserved
	until explicitly removed.

	Args:
	    dry_run: If True, only report what would be migrated without making changes
	    skip_backup: If True, skip creating a backup (not recommended)

	Returns:
	    Dictionary with migration statistics
	"""
	# Check if vault is configured
	if not frappe.conf.get("vault_password_fields_enabled") and not frappe.conf.get(
		"enable_vault_user_passwords"
	):
		print("OpenBao is not enabled in site_config. Skipping migration.")
		print("Set 'vault_password_fields_enabled' and/or 'enable_vault_user_passwords' to true first.")
		return {"skipped": True, "reason": "vault_not_enabled"}

	# Check vault connectivity
	try:
		client = get_vault_client()
		if not client.is_available():
			print("OpenBao is not available. Skipping migration.")
			print("Ensure OpenBao is running and BAO_TOKEN is set.")
			return {"skipped": True, "reason": "vault_not_available"}
	except Exception as e:
		print(f"Cannot connect to OpenBao: {e}")
		return {"skipped": True, "reason": str(e)}

	# Get all entries from __Auth table
	Auth = frappe.qb.Table("__Auth")
	auth_entries = (
		frappe.qb.from_(Auth)
		.select(Auth.doctype, Auth.name, Auth.fieldname, Auth.password, Auth.encrypted)
		.where(Auth.password.isnotnull())
		.where(Auth.password != "")
		.run(as_dict=True)
	)

	if not auth_entries:
		print("No passwords found in __Auth table. Nothing to migrate.")
		return {"migrated": 0, "failed": 0, "skipped": 0}

	print(f"Found {len(auth_entries)} password entries to migrate.")

	# Create backup before migration
	if not dry_run and not skip_backup:
		backup_file = backup_auth_table()
		if backup_file:
			print(f"Backup created: {backup_file}")
		else:
			print("Warning: Could not create backup (no entries found)")

	if dry_run:
		print("\n[DRY RUN] Would migrate:")
		for entry in auth_entries:
			print(f"  - {entry['doctype']}/{entry['name']}/{entry['fieldname']}")
		return {"would_migrate": len(auth_entries)}

	stats = {"migrated": 0, "failed": 0, "already_exists": 0}

	for entry in auth_entries:
		doctype = entry["doctype"]
		name = entry["name"]
		fieldname = entry["fieldname"]
		password = entry["password"]

		try:
			# Check if already in vault
			existing = client.get_secret(doctype, name, fieldname)
			if existing:
				print(f"  [SKIP] {doctype}/{name}/{fieldname} - already in OpenBao")
				stats["already_exists"] += 1
				continue

			# Store in vault
			client.set_secret(doctype, name, fieldname, password)
			print(f"  [OK] {doctype}/{name}/{fieldname}")
			stats["migrated"] += 1

		except VaultError as e:
			print(f"  [FAIL] {doctype}/{name}/{fieldname} - {e}")
			stats["failed"] += 1

	print("\nMigration complete:")
	print(f"  Migrated: {stats['migrated']}")
	print(f"  Already in OpenBao: {stats['already_exists']}")
	print(f"  Failed: {stats['failed']}")

	if stats["failed"] > 0:
		print("\nSome migrations failed. The original passwords are still in __Auth.")
		print("Re-run migration after fixing the issues.")

	return stats


def cleanup_migrated_passwords(confirm: bool = False) -> dict:
	"""
	Remove passwords from __Auth that have been successfully migrated to OpenBao.

	This should only be run after verifying the migration was successful.

	Args:
	    confirm: Must be True to actually delete entries

	Returns:
	    Dictionary with cleanup statistics
	"""
	if not confirm:
		print("This will DELETE passwords from the __Auth table.")
		print("Only run this after verifying migration was successful.")
		print("Call with confirm=True to proceed.")
		return {"deleted": 0}

	client = get_vault_client()

	Auth = frappe.qb.Table("__Auth")
	auth_entries = (
		frappe.qb.from_(Auth)
		.select(Auth.doctype, Auth.name, Auth.fieldname)
		.where(Auth.password.isnotnull())
		.where(Auth.password != "")
		.run(as_dict=True)
	)

	stats = {"deleted": 0, "kept": 0}

	for entry in auth_entries:
		doctype = entry["doctype"]
		name = entry["name"]
		fieldname = entry["fieldname"]

		# Only delete if it exists in vault
		vault_value = client.get_secret(doctype, name, fieldname)
		if vault_value:
			frappe.db.delete(
				"__Auth",
				{"doctype": doctype, "name": name, "fieldname": fieldname},
			)
			print(f"  [DELETED] {doctype}/{name}/{fieldname}")
			stats["deleted"] += 1
		else:
			print(f"  [KEPT] {doctype}/{name}/{fieldname} - not found in OpenBao")
			stats["kept"] += 1

	frappe.db.commit()
	print(f"\nCleanup complete: {stats['deleted']} deleted, {stats['kept']} kept")
	return stats
