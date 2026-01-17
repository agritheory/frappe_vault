<!-- Copyright (c) 2025, AgriTheory and contributors
For license information, please see license.txt-->

# Frappe Vault Site Configuration

## Site Config Options

Add the following keys to your site's configuration file (`/sites/{site_name}/site_config.json`):

```json
{
  // Enable OpenBao storage for encrypted Password fields (API keys, secrets)
  "enable_vault_secrets": true,

  // Enable OpenBao storage for user login passwords (hashed)
  "enable_vault_user_passwords": true,

  // OpenBao server URL
  // Can also be set via BAO_ADDR or VAULT_ADDR environment variable
  "vault_url": "http://localhost:8200",

  // OpenBao authentication token
  // RECOMMENDED: Use BAO_TOKEN or VAULT_TOKEN environment variable instead for production
  "vault_token": "bao.xxxxxxxxxxxxx",

  // Whether to verify SSL certificates when connecting to OpenBao
  // Set to true in production with proper TLS certificates
  "vault_verify_ssl": false
}
```

## Configuration Options Reference

### Core Settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enable_vault_secrets` | boolean | `false` | Enable OpenBao for encrypted Password fields |
| `enable_vault_user_passwords` | boolean | `false` | Enable OpenBao for user login passwords |
| `vault_url` | string | `http://localhost:8200` | OpenBao server URL |
| `vault_token` | string | - | OpenBao authentication token |
| `vault_verify_ssl` | boolean | `true` | Verify SSL certificates |

### Proxy API Settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `vault_proxy_enabled` | boolean | `false` | Enable the Vault proxy API for external access |
| `vault_allowed_roles` | list | `["System Manager"]` | Roles allowed to use the proxy API |

## Environment Variables

For production deployments, use environment variables instead of storing secrets in site_config:

| Variable | Description |
|----------|-------------|
| `BAO_ADDR` | OpenBao server URL (preferred, overrides `vault_url`) |
| `BAO_TOKEN` | OpenBao authentication token (preferred, overrides `vault_token`) |
| `VAULT_ADDR` | Legacy: OpenBao server URL (for backward compatibility) |
| `VAULT_TOKEN` | Legacy: OpenBao authentication token (for backward compatibility) |

**Note**: `BAO_*` environment variables take precedence over `VAULT_*` variables. Both are supported for backward compatibility with existing HashiCorp Vault deployments.

Example supervisor configuration:
```ini
[program:frappe-bench-web]
environment=BAO_TOKEN="bao.xxxxxxxxxxxxx",BAO_ADDR="https://localhost:8200"
```

## Proxy API for External Access

The Vault proxy API allows external tools (like deployment automation) to interact with OpenBao through Frappe's authentication system. External tools authenticate via Frappe's OAuth provider and call the API - they never interact with OpenBao directly.

### Enabling the Proxy

Add to `site_config.json`:
```json
{
  "vault_proxy_enabled": true,
  "vault_allowed_roles": ["System Manager", "Vault Admin"]
}
```

### API-Compatible Route (`/v1/*`)

Frappe Vault provides a Vault/OpenBao API-compatible route at `/v1/*`. This allows existing Vault clients and tools to work with minimal changes.

```bash
# Example: List secrets using curl with Frappe OAuth token
curl -H "Authorization: Bearer $FRAPPE_TOKEN" \
     https://mysite.com/v1/secret/metadata/frappe?list=true

# Example: Read a secret
curl -H "Authorization: Bearer $FRAPPE_TOKEN" \
     https://mysite.com/v1/secret/data/frappe/myapp/config
```

**Key differences from native Vault API:**
- Authentication uses Frappe OAuth Bearer token (not `X-Vault-Token`)
- Certain sensitive endpoints are blocked (seal, unseal, init, token/create)

### Setting Up OAuth Access

1. Create an OAuth Client in Frappe (Setup > Integrations > OAuth Client)
2. Configure your external tool with the client credentials
3. Authenticate and obtain an access token
4. Call the `/v1/*` endpoints with the Bearer token

### Using with Python (hvac-like)

```python
import requests

# Authenticate with Frappe OAuth
token_response = requests.post(
    "https://mysite.com/api/method/frappe.integrations.oauth2.get_token",
    data={
        "grant_type": "client_credentials",
        "client_id": "your-client-id",
        "client_secret": "your-client-secret",
    }
)
access_token = token_response.json()["access_token"]
headers = {"Authorization": f"Bearer {access_token}"}

# List secrets (Vault API compatible)
response = requests.get(
    "https://mysite.com/v1/secret/metadata/frappe?list=true",
    headers=headers,
)
print(response.json())

# Read a secret
response = requests.get(
    "https://mysite.com/v1/secret/data/frappe/User/admin/api_key",
    headers=headers,
)
print(response.json())

# Write a secret
response = requests.post(
    "https://mysite.com/v1/secret/data/myapp/config",
    headers=headers,
    json={"data": {"api_key": "secret123"}}
)
print(response.json())
```

### Frappe Method Endpoints (Alternative)

For Frappe-native integrations, method endpoints are also available:

| Endpoint | Description |
|----------|-------------|
| `frappe_vault.vault_proxy.status` | Check if proxy is enabled (no auth required) |
| `frappe_vault.vault_proxy.health` | Check OpenBao health status |
| `frappe_vault.vault_proxy.list_secrets` | List secrets at a path |
| `frappe_vault.vault_proxy.get_secret_metadata` | Get secret metadata (not values) |
| `frappe_vault.vault_proxy.delete_secret` | Delete a secret |
| `frappe_vault.vault_proxy.proxy_request` | Generic proxy for advanced use cases |

### Security Notes

- All proxy access is logged to Frappe's Activity Log for audit purposes
- Certain sensitive OpenBao endpoints (seal, unseal, init, token create) are blocked
- The proxy only works when `vault_proxy_enabled` is `true`
- Users must have one of the roles in `vault_allowed_roles`

## API Compatibility

OpenBao maintains API compatibility with HashiCorp Vault OSS v1.14.x. The secret paths and HTTP API endpoints are identical, which means:

- Existing site configurations using `vault_url` and `vault_token` continue to work
- The KV v2 secrets engine paths are unchanged
- HTTP headers (`X-Vault-Token`) remain the same

## OpenBao Secret Paths

Secrets are stored in OpenBao's KV v2 secrets engine at the following path structure:

```
secret/data/frappe/{doctype}/{docname}/{fieldname}
```

Examples:
- User password: `secret/data/frappe/User/Administrator/password`
- API secret: `secret/data/frappe/User/admin@example.com/api_secret`
- Integration key: `secret/data/frappe/Integration Settings/Stripe/api_key`

## Field-Level Configuration

By default, when `enable_vault_secrets` is `true`, all Password fields use OpenBao storage. You can opt-out specific fields by setting `vault_enabled` to `false` on the field definition:

```json
{
  "fieldname": "legacy_password",
  "fieldtype": "Password",
  "vault_enabled": false
}
```

## Migration

When enabling OpenBao on an existing site:

1. Existing passwords in the `__Auth` table will continue to work
2. New passwords will be stored in OpenBao
3. When a user changes their password, it moves to OpenBao
4. The old entry is removed from `__Auth`

To migrate all passwords at once, you can use:

```python
# bench console
from frappe.utils.password import update_password
import frappe

# For each user, reset their password to move it to OpenBao
for user in frappe.get_all("User", filters={"enabled": 1}):
    # This requires knowing or resetting passwords
    pass
```

## Troubleshooting

### Check if OpenBao is being used

```python
# bench console
from frappe.utils.password import get_decrypted_password, set_encrypted_password
print("get_decrypted_password from:", get_decrypted_password.__module__)
print("set_encrypted_password from:", set_encrypted_password.__module__)
# Should show "frappe_vault" if patches are active
```

### Check OpenBao connectivity

```python
# bench console
from frappe_vault.vault_client import get_vault_client
client = get_vault_client()
print("OpenBao available:", client.is_available())
print("Health:", client.check_health())
```

### Verify password storage location

```python
# bench console
import frappe

# Check __Auth table
result = frappe.db.sql("""
    SELECT doctype, name, fieldname FROM `__Auth`
    WHERE doctype='User' AND name='Administrator'
""", as_dict=True)
print("In __Auth:", result)

# Check OpenBao
from frappe_vault.vault_client import get_vault_client
client = get_vault_client()
vault_pw = client.get_secret("User", "Administrator", "password")
print("In OpenBao:", "Yes" if vault_pw else "No")
```
