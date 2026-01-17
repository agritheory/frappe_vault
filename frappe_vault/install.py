# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import os
import shutil
import subprocess
from getpass import getpass
from sys import platform

import frappe


def is_root():
	return os.geteuid() == 0


def test_sudo():
	args = "sudo -S echo OK".split()
	kwargs = dict(stdout=subprocess.PIPE, encoding="utf-8")
	cmd = subprocess.run(args, **kwargs)
	return "OK" in cmd.stdout


def install_package(module, pwd=""):
	args = f"sudo -S apt-get -y install {module}".split()
	kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
	if pwd:
		kwargs.update(input=pwd)
	cmd = subprocess.run(args, **kwargs)
	return cmd.stdout, cmd.stderr


def check_openbao_installed():
	"""Check if bao command is available."""
	return shutil.which("bao") is not None


def install_openbao():
	"""Install OpenBao if not present.

	OpenBao is an open-source fork of HashiCorp Vault (MPL-2.0 licensed)
	governed by the Open Source Security Foundation (OpenSSF).
	See: https://openbao.org
	"""
	if check_openbao_installed():
		print("OpenBao is already installed.")
		return

	if platform != "linux":
		print("You need to manually install OpenBao.\n" "Visit: https://openbao.org/docs/install")
		return

	has_sudo_permissions = is_root() or test_sudo()
	pwd = ""
	if not has_sudo_permissions:
		pwd = getpass("Provide sudo password to install OpenBao: ")

	try:
		# Add OpenBao GPG key and repository
		print("Adding OpenBao repository...")
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

		# Install openbao
		out, err = install_package("openbao", pwd)
		if err and "already" not in err.lower():
			print(f"There was an error installing OpenBao: {err}")
		if out:
			print(f"OpenBao installation: {out}")

		if check_openbao_installed():
			print("OpenBao installed successfully.")
		else:
			print(
				"OpenBao installation may have failed. Please install manually:\n"
				"https://openbao.org/docs/install"
			)

	except Exception as e:
		print(f"There was an error installing OpenBao: {e}")
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
		"You will need to add an OpenBao program section to your supervisor config.\n"
		"Example:\n"
		"  [program:openbao]\n"
		"  command=bao server -config=/etc/frappe/openbao/config.hcl\n"
		"  autostart=true\n"
		"  autorestart=true\n"
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
	install_openbao()
	check_openbao_supervisor_config()
