# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

"""OpenBao binary discovery and installation for install hooks and bench commands."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import urllib.request


def check_openbao_installed() -> bool:
	"""Return True when the bao binary exists or OpenBao responds on the default port."""
	if shutil.which("bao") is not None:
		return True
	for candidate in ("/usr/local/bin/bao", "/usr/bin/bao", "/opt/openbao/bin/bao"):
		if os.path.isfile(candidate):
			return True
	try:
		import frappe

		vault_url = frappe.conf.get("vault_url", "http://localhost:8200")
		with urllib.request.urlopen(f"{vault_url}/v1/sys/health", timeout=2) as response:
			return response.status in (200, 429, 472, 473, 501, 503)
	except Exception:
		return False


def ensure_bao_binary(*, required: bool = True) -> str | None:
	"""Return the path to bao, downloading from GitHub releases when absent."""
	bao = shutil.which("bao")
	if not bao:
		for candidate in ("/usr/local/bin/bao", "/usr/bin/bao", "/opt/openbao/bin/bao"):
			if os.path.isfile(candidate):
				bao = candidate
				break

	if bao:
		return bao

	print("'bao' not found. Downloading OpenBao from GitHub releases...")
	try:
		request = urllib.request.Request(
			"https://api.github.com/repos/openbao/openbao/releases/latest",
			headers={"Accept": "application/vnd.github+json"},
		)
		with urllib.request.urlopen(request, timeout=15) as response:
			release = json.loads(response.read())

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
			asset_names = [asset["name"] for asset in release.get("assets", [])]
			raise RuntimeError(f"No Linux x86_64 tar.gz asset in release {tag}. Available: {asset_names}")

		print(f"Downloading {tar_name} ...")
		tar_path = f"/tmp/bao_{version}.tar.gz"
		urllib.request.urlretrieve(tar_url, tar_path)

		extract_dir = f"/tmp/bao_{version}_extract"
		os.makedirs(extract_dir, exist_ok=True)
		with tarfile.open(tar_path, "r:gz") as archive:
			binary_member = next(
				(
					member
					for member in archive.getmembers()
					if member.name in ("bao", "./bao") or member.name.endswith("/bao")
				),
				None,
			)
			if not binary_member:
				raise RuntimeError(
					f"No bao binary in tarball. Contents: {[member.name for member in archive.getmembers()]}"
				)
			binary_member.name = os.path.basename(binary_member.name)
			archive.extract(binary_member, extract_dir)

		dest = "/usr/local/bin/bao"
		subprocess.run(["sudo", "mv", os.path.join(extract_dir, "bao"), dest], check=True)
		subprocess.run(["sudo", "chmod", "+x", dest], check=True)
		print(f"OpenBao {version} installed to {dest}.")
		return dest

	except Exception as error:
		message = f"Could not install OpenBao automatically: {error}"
		if required:
			raise RuntimeError(message) from error
		print(message)
		print("Install manually: https://openbao.org/docs/install")
		return None
