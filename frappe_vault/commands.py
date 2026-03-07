# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import json
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import zipfile

import click
import frappe
from frappe.commands import get_site, pass_context
from frappe.utils import get_bench_path

from frappe_vault.install import cleanup_migrated_passwords as do_cleanup
from frappe_vault.install import migrate_passwords_to_vault as do_migrate
from frappe_vault.install import restore_auth_backup as do_restore
from frappe_vault.vault_client import get_vault_client


# ---------------------------------------------------------------------------
# Helpers shared across commands
# ---------------------------------------------------------------------------


def ensure_bao_binary() -> str:
	"""Return the path to the bao binary, downloading it from GitHub if absent."""
	bao = shutil.which("bao")
	if not bao:
		for candidate in ("/usr/local/bin/bao", "/usr/bin/bao", "/opt/openbao/bin/bao"):
			if os.path.isfile(candidate):
				bao = candidate
				break

	if bao:
		return bao

	# Not found anywhere — download the pre-built binary from GitHub releases.
	click.echo("'bao' not found. Downloading OpenBao from GitHub releases...")
	try:
		req = urllib.request.Request(
			"https://api.github.com/repos/openbao/openbao/releases/latest",
			headers={"Accept": "application/vnd.github+json"},
		)
		with urllib.request.urlopen(req, timeout=15) as r:
			release = json.loads(r.read())

		tag = release["tag_name"]
		version = tag.lstrip("v")

		# Linux releases ship as .tar.gz (not .zip — that's Windows only).
		# The filename uses a capitalised OS name, e.g. bao_2.5.1_Linux_x86_64.tar.gz.
		# Exclude the HSM variant (bao-hsm_*).
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
			asset_names = [a["name"] for a in release.get("assets", [])]
			raise RuntimeError(f"No Linux x86_64 tar.gz asset in release {tag}. Available: {asset_names}")

		click.echo(f"Downloading {tar_name} ...")
		tar_path = f"/tmp/bao_{version}.tar.gz"
		urllib.request.urlretrieve(tar_url, tar_path)

		extract_dir = f"/tmp/bao_{version}_extract"
		os.makedirs(extract_dir, exist_ok=True)
		with tarfile.open(tar_path, "r:gz") as tf:
			# Find the bao binary inside the archive
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
		click.echo(f"OpenBao {version} installed to {dest}.")
		return dest

	except Exception as e:
		click.echo(f"Error: could not install OpenBao automatically: {e}", err=True)
		click.echo("Install manually: https://openbao.org/docs/install", err=True)
		raise SystemExit(1)


def reset_openbao_config(bench_path: str) -> None:
	"""Remove all OpenBao config files and clean up process manager entries."""
	config_dir = os.path.join(bench_path, "config")

	# Config files
	for name in ("openbao.hcl", "openbao-seal.key", "openbao-recovery-keys.txt"):
		path = os.path.join(config_dir, name)
		if os.path.exists(path):
			os.remove(path)
			click.echo(f"Removed {path}")

	# Data directory
	data_dir = os.path.join(config_dir, "openbao-data")
	if os.path.exists(data_dir):
		shutil.rmtree(data_dir)
		click.echo(f"Removed {data_dir}")

	# Remove [program:openbao] block from bench supervisor.conf
	bench_sup_conf = os.path.join(bench_path, "config", "supervisor.conf")
	if os.path.exists(bench_sup_conf):
		content = open(bench_sup_conf).read()
		if "[program:openbao]" in content:
			# Strip the block: everything from the [program:openbao] line to the
			# next blank line that precedes a new [section] or end-of-file.
			cleaned = re.sub(r"\n\[program:openbao\][^\[]*", "", content)
			with open(bench_sup_conf, "w") as f:
				f.write(cleaned)
			click.echo(f"Removed [program:openbao] from {bench_sup_conf}")

	# Remove Procfile entry
	procfile = os.path.join(bench_path, "Procfile")
	if os.path.exists(procfile):
		lines = [l for l in open(procfile).readlines() if not l.startswith("openbao:")]
		with open(procfile, "w") as f:
			f.writelines(lines)


def write_file_secure(path: str, content: str, mode: int = 0o600) -> None:
	parent = os.path.dirname(path)
	if parent and not os.path.exists(parent):
		os.makedirs(parent, mode=0o755)
	fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
	try:
		os.write(fd, content.encode())
	finally:
		os.close(fd)


def generate_openbao_config(seal_key: str, data_path: str) -> str:
	"""Generate openbao.hcl content.

	Args:
	    seal_key: Raw 64-char hex key value (not a file path) for the static seal.
	    data_path: Absolute path to the storage data directory.
	"""
	return f"""# OpenBao configuration for Frappe Vault
# Generated by: bench setup-openbao
# Documentation: https://openbao.org/docs/configuration

ui = true

storage "file" {{
  path = "{data_path}"
}}

listener "tcp" {{
  address     = "127.0.0.1:8200"
  tls_disable = true  # TLS handled by nginx reverse proxy
}}

# Static seal — OpenBao unseals automatically on restart using this inline key.
# The key is also saved to config/openbao-seal.key for disaster recovery.
seal "static" {{
  current_key_id = "frappe-vault-1"
  current_key    = "{seal_key}"
}}

api_addr = "http://127.0.0.1:8200"
"""


def check_openbao_health(vault_addr: str) -> bool:
	"""Return True if OpenBao is responding at vault_addr.

	urllib.request raises HTTPError for non-2xx responses, so we catch it
	explicitly — OpenBao returns 501 when uninitialized and 503 when sealed,
	both of which mean it is running and reachable.
	"""
	try:
		with urllib.request.urlopen(f"{vault_addr}/v1/sys/health", timeout=2) as r:
			return r.status in (200, 429, 472, 473, 501, 503)
	except urllib.error.HTTPError as e:
		return e.code in (200, 429, 472, 473, 501, 503)
	except Exception:
		return False


def wait_for_openbao_health(vault_addr: str, timeout: int = 30) -> bool:
	deadline = time.time() + timeout
	while time.time() < deadline:
		if check_openbao_health(vault_addr):
			return True
		time.sleep(1)
	return False


def check_openbao_initialized(vault_addr: str) -> bool:
	try:
		with urllib.request.urlopen(f"{vault_addr}/v1/sys/init", timeout=5) as r:
			return json.loads(r.read()).get("initialized", False)
	except Exception:
		return False


def init_openbao(vault_addr: str) -> dict:
	result = subprocess.run(
		["bao", "operator", "init", "-recovery-shares=1", "-recovery-threshold=1", "-format=json"],
		env={**os.environ, "BAO_ADDR": vault_addr},
		capture_output=True,
		text=True,
		check=True,
	)
	return json.loads(result.stdout)


def enable_kv_v2(vault_addr: str, token: str) -> None:
	"""Enable the KV v2 secrets engine at secret/. Safe to call if already enabled."""
	result = subprocess.run(
		["bao", "secrets", "enable", "-path=secret", "kv-v2"],
		env={**os.environ, "BAO_ADDR": vault_addr, "BAO_TOKEN": token},
		capture_output=True,
		text=True,
	)
	if result.returncode != 0 and "path is already in use" not in result.stderr:
		raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)


def list_sites(bench_path: str) -> list[str]:
	sites_dir = os.path.join(bench_path, "sites")
	return [
		d
		for d in os.listdir(sites_dir)
		if os.path.isdir(os.path.join(sites_dir, d))
		and not d.startswith("assets")
		and os.path.exists(os.path.join(sites_dir, d, "site_config.json"))
	]


def update_site_config(bench_path: str, site: str, vault_addr: str, token: str) -> None:
	config_path = os.path.join(bench_path, "sites", site, "site_config.json")
	if not os.path.exists(config_path):
		raise FileNotFoundError(f"Site config not found: {config_path}")
	with open(config_path) as f:
		config = json.load(f)
	config.update(
		{
			"vault_url": vault_addr,
			"vault_token": token,
		}
	)
	with open(config_path, "w") as f:
		json.dump(config, f, indent=1)


def detect_production(bench_path: str) -> bool:
	"""Return True when this bench is managed by supervisor.

	Frappe bench's production setup writes a single supervisor.conf inside the
	bench's config/ directory (not a file per program in /etc/supervisor/conf.d/).
	That file is then [include]d by the system supervisord.conf.
	"""
	bench_sup_conf = os.path.join(bench_path, "config", "supervisor.conf")
	return os.path.exists(bench_sup_conf)


def setup_supervisor_config(bench_path: str, config_path: str) -> bool:
	"""Append an OpenBao program block to the bench supervisor.conf.

	Frappe bench manages a single config/supervisor.conf that supervisord
	includes.  We append to that file (owned by the bench user, no sudo needed)
	instead of writing a separate system-wide file.

	Returns True on success, False on error.
	"""
	bench_sup_conf = os.path.join(bench_path, "config", "supervisor.conf")

	# Already present — nothing to do.
	try:
		if "[program:openbao]" in open(bench_sup_conf).read():
			click.echo("OpenBao already present in supervisor.conf.")
			return True
	except FileNotFoundError:
		pass

	bao_bin = shutil.which("bao")
	if not bao_bin:
		# Common install locations when not on PATH
		for candidate in ("/usr/local/bin/bao", "/usr/bin/bao", "/opt/openbao/bin/bao"):
			if os.path.isfile(candidate):
				bao_bin = candidate
				break
	if not bao_bin:
		click.echo(
			"Error: 'bao' binary not found. Install OpenBao and ensure it is on PATH.",
			err=True,
		)
		return False

	log_dir = os.path.join(bench_path, "logs")
	os.makedirs(log_dir, exist_ok=True)

	block = f"""
[program:openbao]
command={bao_bin} server -config={config_path}
priority=1
autostart=true
autorestart=true
directory={bench_path}
stdout_logfile={log_dir}/openbao.log
stderr_logfile={log_dir}/openbao.error.log
user={os.environ.get("USER", "frappe")}
startretries=10
"""

	try:
		with open(bench_sup_conf, "a") as f:
			f.write(block)
	except Exception as e:
		click.echo(f"Warning: could not update {bench_sup_conf}: {e}", err=True)
		return False

	# Inform supervisord about the new program entry so it manages OpenBao
	# on future restarts.  The actual first-run start is handled by
	# start_background() in the main setup flow.
	try:
		subprocess.run(["sudo", "supervisorctl", "reread"], check=True, capture_output=True)
		subprocess.run(["sudo", "supervisorctl", "update"], check=True, capture_output=True)
		click.echo(f"OpenBao added to supervisor config: {bench_sup_conf}")
		return True
	except Exception as e:
		click.echo(f"Warning: could not reload supervisor: {e}", err=True)
		return False


def setup_procfile_entry(bench_path: str) -> None:
	"""Add an openbao line to the bench Procfile if not already present."""
	procfile_path = os.path.join(bench_path, "Procfile")
	entry = "openbao: bench run-openbao\n"
	if os.path.exists(procfile_path):
		with open(procfile_path) as f:
			content = f.read()
		if "openbao" in content:
			click.echo("OpenBao entry already in Procfile.")
			return
		with open(procfile_path, "a") as f:
			f.write(entry)
	else:
		with open(procfile_path, "w") as f:
			f.write(entry)
	click.echo("Added OpenBao to Procfile.")


def start_background(config_path: str) -> None:
	"""Start OpenBao as a detached background process."""
	bao_bin = shutil.which("bao")
	if not bao_bin:
		click.echo("Error: 'bao' not found in PATH.", err=True)
		raise SystemExit(1)
	subprocess.Popen(
		[bao_bin, "server", "-config", config_path],
		stdout=subprocess.DEVNULL,
		stderr=subprocess.DEVNULL,
		start_new_session=True,
	)


# ---------------------------------------------------------------------------
# bench setup-openbao
# ---------------------------------------------------------------------------


@click.command("setup-openbao")
@click.option("--site", "-s", default=None, help="Site to update with vault connection details")
@click.option(
	"--production",
	is_flag=True,
	default=False,
	help="Write supervisor config instead of Procfile entry",
)
@click.option(
	"--reset",
	is_flag=True,
	default=False,
	help="Remove all existing OpenBao config and start completely fresh",
)
def setup_openbao(site=None, production=False, reset=False):
	"""Full OpenBao setup: binary, config, process manager, init, KV engine, and site config.

	For development benches this adds OpenBao to the Procfile (bench start).
	For production benches (auto-detected via supervisor config, or --production flag)
	this writes to the bench supervisor.conf and starts OpenBao immediately.

	If the bench has exactly one site and --site is omitted, vault_url and vault_token
	are written to that site's site_config.json automatically. Feature flags such as
	vault_secrets_api_enabled must be set explicitly by an administrator.

	Use --reset to wipe all existing OpenBao config files and start completely fresh.
	"""
	vault_addr = "http://127.0.0.1:8200"
	bench_path = get_bench_path()
	config_dir = os.path.join(bench_path, "config")
	data_dir = os.path.join(config_dir, "openbao-data")
	config_path = os.path.join(config_dir, "openbao.hcl")
	seal_key_path = os.path.join(config_dir, "openbao-seal.key")

	# ------------------------------------------------------------------
	# Step 0: Ensure bao binary is available; optionally reset state
	# ------------------------------------------------------------------
	if reset:
		click.echo("Resetting OpenBao configuration...")
		reset_openbao_config(bench_path)

	ensure_bao_binary()

	# ------------------------------------------------------------------
	# Step 1: Generate config files
	# ------------------------------------------------------------------
	if os.path.exists(config_path):
		click.echo(f"Using existing config: {config_path}")
	else:
		# Reuse an existing seal key if one was kept (e.g. reset without --reset),
		# otherwise generate a fresh one.
		if os.path.exists(seal_key_path):
			key = open(seal_key_path).read().strip()
			click.echo(f"Reusing existing seal key: {seal_key_path}")
		else:
			key = secrets.token_hex(32)
			write_file_secure(seal_key_path, key + "\n", mode=0o600)
			click.echo(f"Seal key: {seal_key_path}")
		os.makedirs(data_dir, mode=0o700, exist_ok=True)
		write_file_secure(config_path, generate_openbao_config(key, data_dir), mode=0o644)
		click.echo(f"Config:   {config_path}")
		click.echo(f"Data dir: {data_dir}")

	# ------------------------------------------------------------------
	# Step 2: Register with process manager
	# ------------------------------------------------------------------
	use_production = production or detect_production(bench_path)
	if use_production:
		setup_supervisor_config(bench_path, config_path)
	else:
		setup_procfile_entry(bench_path)

	# ------------------------------------------------------------------
	# Step 3: Start OpenBao
	# ------------------------------------------------------------------
	if check_openbao_health(vault_addr):
		click.echo("OpenBao is already running.")
	else:
		click.echo("Starting OpenBao...")
		# Always start the process directly in the background for the initial
		# setup — this is reliable regardless of how supervisord is wired up.
		# The supervisor / Procfile entry registered above ensures it is
		# managed (auto-restart, start on boot) from this point forward.
		start_background(config_path)

		click.echo("Waiting for OpenBao to be ready...", nl=False)
		if not wait_for_openbao_health(vault_addr, timeout=30):
			click.echo(" timed out.", err=True)
			click.echo("OpenBao did not start within 30 seconds.", err=True)
			raise SystemExit(1)
		click.echo(" ready.")

	# ------------------------------------------------------------------
	# Step 4: Initialize (first time only)
	# ------------------------------------------------------------------
	if check_openbao_initialized(vault_addr):
		click.echo("OpenBao is already initialized.")
		return

	click.echo("Initializing OpenBao...")
	init_data = init_openbao(vault_addr)
	root_token = init_data["root_token"]
	recovery_keys = init_data.get("recovery_keys") or init_data.get("unseal_keys_b64", [])

	# Save recovery keys with tight permissions
	recovery_file = os.path.join(config_dir, "openbao-recovery-keys.txt")
	write_file_secure(
		recovery_file,
		"\n".join(f"Recovery Key {i + 1}: {k}" for i, k in enumerate(recovery_keys))
		+ f"\n\nRoot Token: {root_token}\n",
		mode=0o600,
	)
	click.echo(f"Recovery keys saved to: {recovery_file}")
	click.echo(
		"Store the recovery keys securely offline — they are only needed for disaster recovery."
	)

	# ------------------------------------------------------------------
	# Step 5: Enable KV v2 secrets engine
	# ------------------------------------------------------------------
	click.echo("Enabling KV v2 secrets engine...")
	enable_kv_v2(vault_addr, root_token)
	click.echo("KV v2 enabled at secret/")

	# ------------------------------------------------------------------
	# Step 6: Update site config
	# ------------------------------------------------------------------
	target_site = site
	if not target_site:
		sites = list_sites(bench_path)
		if len(sites) == 1:
			target_site = sites[0]

	if target_site:
		update_site_config(bench_path, target_site, vault_addr, root_token)
		click.echo(f"Updated site config: {target_site}")
	else:
		click.echo("\nAdd the following to your site_config.json:")
		click.echo(f'  "vault_url": "{vault_addr}",')
		click.echo(f'  "vault_token": "{root_token}"')

	click.echo("\nOpenBao setup complete.")


# ---------------------------------------------------------------------------
# bench run-openbao  (used by Procfile / bench start)
# ---------------------------------------------------------------------------


@click.command("run-openbao")
def run_openbao():
	"""Start the OpenBao server process (invoked by Procfile via bench start)."""
	bench_path = get_bench_path()
	config_path = os.path.join(bench_path, "config", "openbao.hcl")

	if not os.path.exists(config_path):
		click.echo("Error: OpenBao config not found. Run 'bench setup-openbao' first.", err=True)
		raise SystemExit(1)

	bao_bin = shutil.which("bao")
	if not bao_bin:
		click.echo("Error: 'bao' binary not found in PATH.", err=True)
		raise SystemExit(1)

	# Replace the current process with bao so bench can manage its lifecycle
	os.execv(bao_bin, [bao_bin, "server", "-config", config_path])


# ---------------------------------------------------------------------------
# bench vault-status
# ---------------------------------------------------------------------------


@click.command("vault-status")
@pass_context
def vault_status(context):
	"""Check OpenBao connectivity and configuration status."""
	site = get_site(context)
	frappe.init(site=site)
	frappe.connect()

	try:
		click.echo(f"Site: {site}")
		click.echo(
			f"vault_password_fields_enabled: {frappe.conf.get('vault_password_fields_enabled', False)}"
		)
		click.echo(
			f"enable_vault_user_passwords:   {frappe.conf.get('enable_vault_user_passwords', False)}"
		)
		click.echo(
			f"vault_secrets_api_enabled:     {frappe.conf.get('vault_secrets_api_enabled', False)}"
		)
		click.echo(f"vault_url:                     {frappe.conf.get('vault_url', 'not set')}")
		click.echo(f"vault_proxy_enabled:           {frappe.conf.get('vault_proxy_enabled', False)}")

		try:
			client = get_vault_client()
			available = client.is_available()
			click.echo(f"\nOpenBao: {'✓ connected' if available else '✗ not available'}")
			if available:
				health = client.check_health()
				click.echo(f"  Initialized: {health.get('initialized', 'unknown')}")
				click.echo(f"  Sealed:      {health.get('sealed', 'unknown')}")
		except Exception as e:
			click.echo(f"\nOpenBao: ✗ error — {e}")

		auth_count = frappe.db.sql("SELECT COUNT(*) FROM `__Auth` WHERE `password` IS NOT NULL")[0][0]
		click.echo(f"\nPasswords in __Auth table: {auth_count}")

	finally:
		frappe.destroy()


# ---------------------------------------------------------------------------
# bench migrate-passwords-to-vault
# ---------------------------------------------------------------------------


@click.command("migrate-passwords-to-vault")
@click.option("--dry-run", is_flag=True, help="Show what would be migrated without making changes")
@click.option("--skip-backup", is_flag=True, help="Skip creating a backup (not recommended)")
@pass_context
def migrate_passwords_to_vault(context, dry_run=False, skip_backup=False):
	"""Migrate existing passwords from __Auth table to OpenBao."""
	site = get_site(context)
	frappe.init(site=site)
	frappe.connect()

	try:
		do_migrate(dry_run=dry_run, skip_backup=skip_backup)
	finally:
		frappe.destroy()


# ---------------------------------------------------------------------------
# bench restore-auth-backup
# ---------------------------------------------------------------------------


@click.command("restore-auth-backup")
@click.argument("backup_file")
@pass_context
def restore_auth_backup(context, backup_file):
	"""Restore __Auth table from a backup file created during migration."""
	site = get_site(context)
	frappe.init(site=site)
	frappe.connect()

	try:
		do_restore(backup_file)
	finally:
		frappe.destroy()


# ---------------------------------------------------------------------------
# bench cleanup-migrated-passwords
# ---------------------------------------------------------------------------


@click.command("cleanup-migrated-passwords")
@click.option("--confirm", is_flag=True, help="Actually delete the migrated passwords from __Auth")
@pass_context
def cleanup_migrated_passwords(context, confirm=False):
	"""Remove passwords from __Auth that have been migrated to OpenBao."""
	site = get_site(context)
	frappe.init(site=site)
	frappe.connect()

	try:
		do_cleanup(confirm=confirm)
	finally:
		frappe.destroy()


# ---------------------------------------------------------------------------
# bench generate-seal-key  (key generation only, without full setup)
# ---------------------------------------------------------------------------


@click.command("generate-seal-key")
@click.option("--output", "-o", type=click.Path(), help="Write key to file (0600 permissions)")
def generate_seal_key(output=None):
	"""Generate a cryptographically secure seal key for OpenBao static seal.

	For full automated setup use 'bench setup-openbao' instead.
	"""
	key = secrets.token_hex(32)

	if output:
		try:
			write_file_secure(output, key + "\n", mode=0o600)
			click.echo(f"Seal key written to: {output} (0600)")
		except PermissionError:
			click.echo(f"Error: permission denied writing to {output}", err=True)
			raise SystemExit(1)
	else:
		bench_path = get_bench_path()
		click.echo(f"Key: {key}")
		click.echo()
		click.echo("Use 'bench setup-openbao' for full automated setup, or add manually:")
		click.echo()
		click.echo('seal "static" {')
		click.echo('  current_key_id = "frappe-vault-1"')
		click.echo(f'  current_key = "file://{bench_path}/config/openbao-seal.key"')
		click.echo("}")


commands = [
	setup_openbao,
	run_openbao,
	vault_status,
	migrate_passwords_to_vault,
	cleanup_migrated_passwords,
	restore_auth_backup,
	generate_seal_key,
]
