# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""
Custom Frappe page renderer for the /v1/* Vault API proxy.

Registered via hooks.py::page_renderer.  Frappe's PathResolver checks custom
renderers before any built-in renderer, so this intercepts every request whose
path starts with "v1/" and returns a real Werkzeug JSON Response — bypassing
the HTML template engine entirely.

DELETE-method handling
----------------------
Frappe's application() (frappe/app.py) only routes GET / HEAD / POST to
get_response() and therefore to page renderers.  DELETE raises NotFound before
our renderer is ever called.

The fix: the companion before_request hook handle_vault_delete raises a
VaultApiResponse (a werkzeug.exceptions.HTTPException subclass).  Exceptions
raised inside init_request (where before_request hooks run) propagate up to
app.py's top-level try/except block:

    except HTTPException as e:
        return e          # ← our response is returned here

This fires before the `elif request.method in ("GET", "HEAD", "POST")` check,
so DELETE requests to /v1/* are handled correctly.

Note: werkzeug.Request.method is a cached_property — modifying
environ["REQUEST_METHOD"] after first access has no effect.  Raising an
exception is the only reliable way to short-circuit the request.
"""

import json

import frappe
from werkzeug.exceptions import HTTPException
from werkzeug.wrappers import Response

from frappe_vault.vault_client import VaultConnectionError, VaultError, get_vault_client

BLOCKED_PATH_PREFIXES = (
	"/v1/sys/seal",
	"/v1/sys/unseal",
	"/v1/sys/init",
	"/v1/sys/rekey",
	"/v1/sys/rotate",
	"/v1/auth/token/create",
	"/v1/auth/token/revoke",
)


class VaultApiResponse(HTTPException):
	"""Werkzeug HTTPException that carries a JSON payload.

	Raising this from a before_request hook causes app.py to execute:
	    except HTTPException as e: return e
	which returns our response before the method-check branch is reached.
	"""

	def __init__(self, data: dict, status: int) -> None:
		super().__init__(data, status)
		self.data = data
		self.code = status

	def get_response(self, environ=None):
		return Response(json.dumps(self.data), status=self.code, mimetype="application/json")


def json_response(data: dict, status: int = 200) -> Response:
	return Response(json.dumps(data), status=status, mimetype="application/json")


def check_access(vault_path: str) -> Response | None:
	"""Return an error Response if the caller should be denied, else None."""
	if frappe.session.user == "Guest":
		return json_response({"errors": ["permission denied"]}, 401)

	if not frappe.conf.get("vault_proxy_enabled", False):
		return json_response({"errors": ["Vault proxy is not enabled"]}, 403)

	if frappe.session.user != "Administrator":
		allowed_roles = frappe.conf.get("vault_allowed_roles", ["System Manager"])
		if not set(allowed_roles) & set(frappe.get_roles(frappe.session.user)):
			return json_response({"errors": ["permission denied"]}, 403)

	for prefix in BLOCKED_PATH_PREFIXES:
		if vault_path.startswith(prefix):
			audit(vault_path, False, "blocked path")
			return json_response(
				{"errors": [f"Access to {prefix} is not allowed through proxy"]},
				403,
			)

	return None


def proxy(method: str, vault_path: str, body: dict | None = None) -> Response:
	"""Call OpenBao and return a Werkzeug Response mirroring the result."""
	try:
		client = get_vault_client()
		vault_response = client._make_request(method, vault_path, data=body)
	except VaultConnectionError as e:
		audit(vault_path, False, str(e))
		return json_response({"errors": [f"OpenBao unreachable: {e}"]}, 503)
	except VaultError as e:
		audit(vault_path, False, str(e))
		return json_response({"errors": [str(e)]}, 502)

	audit(vault_path, True, f"method={method} status={vault_response.status_code}")

	status = vault_response.status_code
	if status == 204:
		return Response("", status=204, mimetype="application/json")

	try:
		data = vault_response.json()
	except Exception:
		data = {"raw": vault_response.text}

	return json_response(data, status)


def audit(path: str, success: bool, details: str | None = None) -> None:
	"""Write a Vault access record to the Activity Log (best-effort)."""
	try:
		frappe.get_doc(
			{
				"doctype": "Activity Log",
				"user": frappe.session.user,
				"subject": f"Vault proxy: {path}",
				"content": json.dumps({"path": path, "success": success, "details": details}),
				"reference_doctype": "User",
				"reference_name": frappe.session.user,
			}
		).insert(ignore_permissions=True)
	except Exception:
		pass


def authenticate_vault_token() -> None:
	"""auth_hooks handler: accept X-Vault-Token as Frappe API key auth.

	Native Vault/OpenBao clients send X-Vault-Token instead of Frappe's
	Authorization: token header.  This hook bridges them by treating the token
	value as a Frappe api_key:api_secret pair and delegating to the standard
	API key validator.

	Only activates on /v1/* requests so it doesn't interfere with the rest of
	Frappe, and only when no Authorization header is already present (to avoid
	shadowing normal Frappe auth).

	Vault CLI usage:
	    export VAULT_ADDR=http://localhost:8004
	    export VAULT_TOKEN=<frappe_api_key>:<frappe_api_secret>
	    vault kv get secret/frappe/myapp/config
	"""
	request = getattr(frappe.local, "request", None)
	if not request or not request.path.startswith("/v1/"):
		return

	if frappe.get_request_header("Authorization"):
		return

	token = frappe.get_request_header("X-Vault-Token")
	if not token or ":" not in token:
		return

	from frappe.auth import validate_auth_via_api_keys

	validate_auth_via_api_keys(["token", token])


def handle_vault_delete() -> None:
	"""Before-request hook: handle DELETE /v1/* before app.py can raise NotFound.

	Frappe's app.py only routes GET/HEAD/POST to get_response(); DELETE goes to
	`raise NotFound`.  By raising VaultApiResponse (an HTTPException subclass)
	here, we short-circuit the request inside the HTTPException handler before
	the method check is reached.

	NOTE: before_request hooks run inside init_request(), which is called before
	validate_auth().  We call validate_auth() ourselves so that frappe.session.user
	is set correctly before checking access.
	"""
	request = getattr(frappe.local, "request", None)
	if not request or request.method != "DELETE" or not request.path.startswith("/v1/"):
		return

	# Authenticate — validate_auth() normally runs after init_request() returns,
	# but we need the session user set now.
	from frappe.auth import validate_auth

	validate_auth()

	vault_path = "/" + request.path.strip("/")

	deny = check_access(vault_path)
	if deny:
		raise VaultApiResponse(json.loads(deny.data), deny.status_code)

	resp = proxy("DELETE", vault_path)
	raise VaultApiResponse(json.loads(resp.data) if resp.data else {}, resp.status_code)


class VaultApiRenderer:
	"""Custom page renderer that handles /v1/* as a transparent Vault API proxy.

	Frappe's PathResolver checks every renderer registered via the page_renderer
	hook before built-in renderers.  Our renderer wins for all /v1/* paths.

	render() returns a Werkzeug Response with the correct HTTP status code and a
	JSON body matching what OpenBao itself would have returned, allowing any
	native Vault/OpenBao client to work without modification.
	"""

	def __init__(self, path: str, http_status_code: int | None = None) -> None:
		self.path = path  # leading/trailing slashes already stripped by PathResolver
		self.http_status_code = http_status_code or 200

	def can_render(self) -> bool:
		return self.path == "v1" or self.path.startswith("v1/")

	def render(self) -> Response:
		vault_path = "/" + self.path  # e.g. "/v1/sys/health"

		deny = check_access(vault_path)
		if deny:
			return deny

		request = frappe.local.request
		method = request.method  # GET or POST (DELETE handled in handle_vault_delete)

		body = None
		if method in ("POST", "PUT", "PATCH"):
			content_type = request.content_type or ""
			if "application/json" in content_type:
				raw = request.get_data(as_text=True)
				if raw:
					try:
						body = json.loads(raw)
					except json.JSONDecodeError:
						return json_response({"errors": ["invalid JSON body"]}, 400)

		return proxy(method, vault_path, body)
