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
  "vault_token": "bao.xxxxxxxxxxxxx"
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
secret/data/frappe/{site}/{doctype}/{docname}/{fieldname}
```

Secrets are namespaced by site name to support multi-tenant deployments where multiple Frappe sites share the same OpenBao instance.

Examples (for site `erp.example.com`):
- User password: `secret/data/frappe/erp.example.com/User/Administrator/password`
- API secret: `secret/data/frappe/erp.example.com/User/admin@example.com/api_secret`
- Integration key: `secret/data/frappe/erp.example.com/Integration Settings/Stripe/api_key`

This ensures that secrets from different sites never collide, even if they have documents with the same names.

## Migration

When installing Frappe Vault on an existing site with passwords, you need to migrate them to OpenBao.

### Automatic Migration

The migration runs automatically during `bench --site {site} install-app frappe_vault` if vault is enabled in `site_config.json`.

### Manual Migration

You can also run migration manually using bench commands:

```shell
# Check current status
bench --site {site} vault-status

# Preview what would be migrated (dry run)
bench --site {site} migrate-passwords-to-vault --dry-run

# Run the migration
bench --site {site} migrate-passwords-to-vault

# After verifying everything works, optionally clean up __Auth table
bench --site {site} cleanup-migrated-passwords --confirm
```

### Migration Process

1. **migrate-passwords-to-vault**: Copies all passwords from `__Auth` table to OpenBao
   - Skips entries that already exist in OpenBao
   - Does not delete from `__Auth` (safe to re-run)

2. **cleanup-migrated-passwords**: Removes entries from `__Auth` that exist in OpenBao
   - Only run after verifying the migration was successful
   - Requires `--confirm` flag to actually delete

### From bench console

```python
# bench console
from frappe_vault.install import migrate_passwords_to_vault, cleanup_migrated_passwords

# Dry run first
migrate_passwords_to_vault(dry_run=True)

# Run migration
migrate_passwords_to_vault()

# Clean up (after verification)
cleanup_migrated_passwords(confirm=True)
```

## CLI Reference

Frappe Vault provides bench commands for managing the integration.

### vault-status

Check OpenBao connectivity and configuration status.

```shell
bench --site {site} vault-status
```

**Output:**
- Site configuration (`enable_vault_secrets`, `enable_vault_user_passwords`, etc.)
- OpenBao connection status (connected/not available)
- OpenBao health (initialized, sealed status)
- Count of passwords remaining in `__Auth` table

### migrate-passwords-to-vault

Migrate existing passwords from `__Auth` table to OpenBao.

```shell
# Preview what would be migrated (recommended first step)
bench --site {site} migrate-passwords-to-vault --dry-run

# Run the actual migration (creates backup automatically)
bench --site {site} migrate-passwords-to-vault

# Skip backup if needed (not recommended)
bench --site {site} migrate-passwords-to-vault --skip-backup
```

**Options:**
- `--dry-run`: Show what would be migrated without making changes
- `--skip-backup`: Skip creating a backup file (not recommended)

**Behavior:**
- Creates a SQL backup of `__Auth` table using `mysqldump` (stored in `{site}/private/backups/`)
- Copies passwords from `__Auth` to OpenBao
- Skips entries that already exist in OpenBao (safe to re-run)
- Does NOT delete from `__Auth` (preserves original data)
- Reports success/failure for each entry

### restore-auth-backup

Restore `__Auth` table from a SQL backup file if migration causes issues.

```shell
# List available backups
ls sites/{site}/private/backups/__Auth_backup_*.sql

# Restore from a backup
bench --site {site} restore-auth-backup sites/{site}/private/backups/__Auth_backup_20260117_143022.sql

# Or restore directly with mysql
mysql -u root -p {db_name} < sites/{site}/private/backups/__Auth_backup_20260117_143022.sql
```

**Behavior:**
- Uses `mysql` client to restore the SQL dump
- Replaces existing `__Auth` table with backup contents
- Can also be restored manually with `mysql` command

### cleanup-migrated-passwords

Remove passwords from `__Auth` table that have been successfully migrated to OpenBao.

```shell
# Preview what would be deleted
bench --site {site} cleanup-migrated-passwords

# Actually delete (requires --confirm flag)
bench --site {site} cleanup-migrated-passwords --confirm
```

**Options:**
- `--confirm`: Required to actually delete entries (safety measure)

**Behavior:**
- Only deletes entries that exist in both `__Auth` AND OpenBao
- Keeps entries that are NOT in OpenBao (won't cause data loss)
- Run only after verifying migration was successful

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
# Check __Auth table
Auth = frappe.qb.Table("__Auth")
result = (
    frappe.qb.from_(Auth)
    .select(Auth.doctype, Auth.name, Auth.fieldname)
    .where(Auth.doctype == "User")
    .where(Auth.name == "Administrator")
    .run(as_dict=True)
)
print("In __Auth:", result)

# Check OpenBao
from frappe_vault.vault_client import get_vault_client
client = get_vault_client()
vault_pw = client.get_secret("User", "Administrator", "password")
print("In OpenBao:", "Yes" if vault_pw else "No")
```
