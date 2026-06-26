<!-- Copyright (c) 2025, AgriTheory and contributors
For license information, please see license.txt-->

# Frappe Vault Developer Setup

<div class="byline">
  Tyler Matteson 2026-03-07
</div>


Before you begin, make sure that your Python version is 3.10 or later for Frappe version 15.

## Prerequisites

- Python 3.10+
- Node.js 18+
- MariaDB 10.6+
- Redis
- OpenBao (see [What is OpenBao?](#what-is-openbao))

### What is OpenBao?

[OpenBao](https://openbao.org) is an open-source fork of HashiCorp Vault (MPL-2.0 licensed), governed by the Open Source Security Foundation (OpenSSF). It provides identity-based secrets and encryption management with API compatibility to Vault OSS v1.14.x.

## Setup Instructions

1. **Set up a new bench** using [pyenv](https://github.com/pyenv/pyenv) for managing Python environments:

```shell
bench init --frappe-branch version-15 {{ bench name }} --python ~/.pyenv/versions/3.10.12/bin/python3
```

2. **Create a new site**:
```shell
cd {{ bench name }}
bench new-site {{ site name }} --force --db-name {{ site name }}
```

3. **Download the Frappe Vault application**:
```shell
bench get-app frappe_vault https://github.com/agritheory/frappe_vault.git
```

4. **Install the app to your site**:
```shell
bench --site {{ site name }} install-app frappe_vault

# Optional: Check that the app installed on your site
bench --site {{ site name }} list-apps
```

5. **Set developer mode** in `site_config.json`:
```shell
nano sites/{{ site name }}/site_config.json
# Add this line:
  "developer_mode": 1,
```

6. **Add the site to your hosts file**:
```shell
bench --site {{ site name }} add-to-hosts
```

7. **Install OpenBao**:
```shell
# macOS:
brew install openbao

# Linux (download .deb from GitHub releases):
VERSION="2.4.4"  # Check https://github.com/openbao/openbao/releases for latest
wget https://github.com/openbao/openbao/releases/download/v${VERSION}/bao-hsm_${VERSION}_linux_amd64.deb
sudo dpkg -i bao-hsm_${VERSION}_linux_amd64.deb
```

8. **Set up OpenBao** (installs the app first if not done above):
```shell
bench --site {{ site name }} install-app frappe_vault
bench setup-openbao
```

`bench setup-openbao` does everything in one step:
- Generates `config/openbao.hcl` and `config/openbao-seal.key` (static auto-unseal)
- Adds `openbao: bench run-openbao` to the Procfile
- Starts OpenBao and waits for it to be ready
- Initialises OpenBao and saves recovery keys to `config/openbao-recovery-keys.txt` (0600)
- Enables the KV v2 secrets engine at `secret/`
- Writes `vault_url` and `vault_token` to the site config (feature flags are not enabled automatically)

9. **Launch your bench**:
```shell
bench start
```

OpenBao is started automatically on each `bench start` via the Procfile entry added in the previous step.

10. **Set the admin password** (will be stored in OpenBao):
```shell
bench --site {{ site name }} set-admin-password admin
```

### Resetting OpenBao (if needed)

If you need to start fresh with OpenBao:
```shell
# Stop bench first, then remove the generated config files:
rm -rf config/openbao.hcl config/openbao-seal.key config/openbao-data config/openbao-recovery-keys.txt
# Remove the Procfile entry, then re-run setup:
bench setup-openbao
bench start
```

## Development Tools

### Running mypy locally
```shell
source env/bin/activate
mypy ./apps/frappe_vault/frappe_vault --ignore-missing-imports
```

### Running pytest locally
```shell
# Set environment variables for OpenBao
export BAO_ADDR="http://127.0.0.1:8200"
export BAO_TOKEN="bao.xxxxx"

# Run tests
cd apps/frappe_vault
poetry install
pytest frappe_vault/tests/ -v
```

### Viewing OpenBao audit logs

Audit logging is automatically enabled when using `bench setup-openbao`. View the logs:
```shell
# Watch the audit log (each line is JSON)
tail -f logs/openbao-audit.log | jq .

# Or search for specific operations
grep "secret/data" logs/openbao-audit.log | jq .
```

## Testing the Integration

### Test 1: OpenBao Connectivity

```python
# bench console
from frappe_vault.vault_client import get_vault_client

# Test OpenBao connectivity
client = get_vault_client()
print("OpenBao available:", client.is_available())
print("Health:", client.check_health())

# Test secret storage
client.set_secret("Test", "test-doc", "api_key", "my-secret")
print("Retrieved:", client.get_secret("Test", "test-doc", "api_key"))

# Cleanup
client.delete_secret("Test", "test-doc", "api_key")
```

### Test 2: Password Storage in OpenBao

```shell
# Set admin password (stored in OpenBao)
bench --site {{ site name }} set-admin-password testpass123

# Verify it's in OpenBao, not in __Auth table
bench --site {{ site name }} console
```

```python
# Check __Auth table (should be empty for Administrator)
Auth = frappe.qb.Table("__Auth")
result = (
    frappe.qb.from_(Auth)
    .select(Auth.star)
    .where(Auth.name == "Administrator")
    .run(as_dict=True)
)
print("In __Auth:", result)  # Should be empty

# Check OpenBao
from frappe_vault.vault_client import get_vault_client
client = get_vault_client()
vault_pw = client.get_secret("User", "Administrator", "password")
print("In OpenBao:", "Yes (hash starts with $)" if vault_pw else "No")

# Test login works
import frappe.auth
frappe.local.request = frappe._dict(
    path="/", scheme="http",
    cookies=frappe._dict(),
    headers=frappe._dict(),
)
frappe.local.request_ip = "127.0.0.1"
frappe.local.cookie_manager = frappe.auth.CookieManager()
frappe.local.form_dict = frappe._dict()

lm = frappe.auth.LoginManager()
lm.authenticate("Administrator", "Diamo2727")
print(f"Authenticated as: {lm.user}")
```

### Test 3: Proxy API (Method Endpoints)

First, enable the proxy in `site_config.json`:
```json
{
  "vault_proxy_enabled": true
}
```

Then restart bench and test:

```python
# bench console
from frappe_vault import vault_proxy

# Test status (no auth required)
print(vault_proxy.status())

# Test health (requires auth - run as Administrator)
print(vault_proxy.health())

# Test list secrets
print(vault_proxy.list_secrets("frappe"))
```

### Test 4: API-Compatible Route (`/v1/*`)

Test with curl (requires a logged-in session or API key):

```shell
# Get your API keys from User doctype or create them
# Then test the /v1/* route

# Using session cookie (if logged into browser)
curl -b "sid=your-session-id" \
     http://{{ site name }}:8000/v1/sys/health

# Using API key (create in User > API Access)
curl -H "Authorization: token api_key:api_secret" \
     http://{{ site name }}:8000/v1/sys/health

# List secrets
curl -H "Authorization: token api_key:api_secret" \
     "http://{{ site name }}:8000/v1/secret/metadata/frappe?list=true"
```

### Test 5: OAuth Token Flow (for external tools)

```shell
# 1. Create OAuth Client in Frappe UI:
#    Setup > Integrations > OAuth Client
#    - Grant Type: Client Credentials
#    - Note the client_id and client_secret

# 2. Get access token
curl -X POST "http://{{ site name }}:8000/api/method/frappe.integrations.oauth2.get_token" \
     -d "grant_type=client_credentials" \
     -d "client_id=YOUR_CLIENT_ID" \
     -d "client_secret=YOUR_CLIENT_SECRET"

# 3. Use the access token
export TOKEN="your-access-token-from-step-2"

curl -H "Authorization: Bearer $TOKEN" \
     http://{{ site name }}:8000/v1/sys/health

curl -H "Authorization: Bearer $TOKEN" \
     "http://{{ site name }}:8000/v1/secret/metadata/frappe?list=true"
```

### Test 6: Audit Logging

After running proxy requests, check that they're logged:

```python
# bench console
# Check Activity Log for vault operations
logs = frappe.get_all(
    "Activity Log",
    filters={"subject": ["like", "%Vault%"]},
    fields=["user", "subject", "creation"],
    order_by="creation desc",
    limit=10
)
for log in logs:
    print(f"{log.creation} - {log.user}: {log.subject}")
```

### Test 7: Proxy API Permission Checks

Test that users without a `vault_allowed_roles` role cannot use the generic proxy:

```python
# bench console
# Create a test user without System Manager role
if not frappe.db.exists("User", "testuser@example.com"):
    user = frappe.get_doc({
        "doctype": "User",
        "email": "testuser@example.com",
        "first_name": "Test",
        "roles": [{"role": "Desk User"}]  # No System Manager
    })
    user.insert()
    frappe.db.commit()

# Switch to that user
frappe.set_user("testuser@example.com")

# This should fail with PermissionError (no vault_allowed_roles role)
from frappe_vault import vault_proxy
try:
    vault_proxy.health()
    print("ERROR: Should have raised PermissionError")
except frappe.PermissionError:
    print("Correctly denied access!")

# Switch back
frappe.set_user("Administrator")
```

### Test 8: Vault Secrets API and Folder Permissions

Test the Vault Secrets CRUD API and folder-based sharing (requires `vault_secrets_api_enabled: true`):

```python
# bench console
from frappe_vault.frappe_vault import create_secret, get_secret, reveal_secret, share_folder, delete_secret

# Create a secret (folder chain is auto-created)
result = create_secret(
    title="Stripe Key",
    path="apps/myapp/stripe_key",
    value="sk_live_example",
    description="Production Stripe secret key",
)
print("Created:", result["name"])  # → "apps/myapp/stripe_key"

# Read metadata (no value returned)
meta = get_secret("apps/myapp/stripe_key")
print("Permissions:", meta["permissions"])  # → {"read": True, "write": True, "share": True}

# Reveal the actual value (requires read permission + OpenBao access)
data = reveal_secret("apps/myapp/stripe_key")
print("Value:", data["value"])

# Share the folder with a developer
share_folder("apps/myapp", "developer@example.com", read=1, write=0, share=0)

# Verify the developer can now read secrets inside that folder
frappe.set_user("developer@example.com")
meta = get_secret("apps/myapp/stripe_key")
print("Developer permissions:", meta["permissions"])  # → {"read": True, "write": False, "share": False}
frappe.set_user("Administrator")

# Clean up
delete_secret("apps/myapp/stripe_key")
```
