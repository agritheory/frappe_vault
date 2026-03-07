<!-- Copyright (c) 2025, AgriTheory and contributors
For license information, please see license.txt-->

# Frappe Vault Site Configuration

<div class="byline">
  Tyler Matteson 2026-03-07
</div>


## Site Config Options

Add the following keys to your site's configuration file (`/sites/{site_name}/site_config.json`):

```json
{
  // Enable OpenBao storage for Frappe Password fields (API keys, secrets, etc.)
  "vault_password_fields_enabled": true,

  // Enable OpenBao storage for user login passwords (hashed)
  "enable_vault_user_passwords": true,

  // Enable the Vault Secret doctype, CRUD API, and desk UI
  "vault_secrets_api_enabled": true,

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
| `vault_password_fields_enabled` | boolean | `false` | Enable OpenBao storage for Frappe Password-type fields (API keys, retrievable secrets) |
| `enable_vault_user_passwords` | boolean | `false` | Enable OpenBao storage for user login passwords (hashed) |
| `vault_secrets_api_enabled` | boolean | `false` | Enable the Vault Secret doctype, CRUD API, and desk UI |
| `vault_url` | string | `http://localhost:8200` | OpenBao server URL |
| `vault_token` | string | - | OpenBao authentication token |

### Proxy API Settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `vault_proxy_enabled` | boolean | `false` | Enable the Vault proxy API for external access |
| `vault_allowed_roles` | list | `["System Manager"]` | Roles allowed to use the proxy API |

### Multi-Site Sync Settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `vault_sync_enabled` | boolean | `false` | Enable multi-site OpenBao synchronization |
| `vault_remotes` | list | `[]` | Array of remote OpenBao server configurations |

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
- Users must have one of the roles in `vault_allowed_roles` to access broad proxy operations; per-secret access is controlled via Frappe's standard DocShare and role permissions on `Vault Secret` documents

## Vault Secrets Management

The Vault Secrets API provides a structured way to store and access named secrets through Frappe's permission system. Unlike the generic proxy API (which operates on raw OpenBao paths), Vault Secrets are first-class Frappe documents (`Vault Secret` doctype) with standard role, owner, and DocShare permissions.

### Enabling the Vault Secrets API

Add to `site_config.json`:
```json
{
  "vault_secrets_api_enabled": true
}
```


### The Vault Secret Document

Each secret is a `Vault Secret` document whose name equals its `path` field (e.g. `ci-cd/github/actions_token`).

| Field | Description |
|-------|-------------|
| `title` | Human-readable label |
| `path` | Unique path in OpenBao; also used as the document name (`autoname: field:path`) |
| `folder` | Parent folder (Link to a `Vault Secret` with `is_folder = 1`) |
| `is_folder` | When checked, this document is a folder — it holds no secret value |
| `description` | Optional free-text description |
| `secret_value` | Password field — write-only in the UI; read back via `reveal_secret` |

### Folder Hierarchy

Secrets are organised in a tree, modelled after Frappe's `File` doctype:

```
apps/                            ← Vault Secret, is_folder=1
└── myapp/                       ← Vault Secret, is_folder=1
    ├── stripe_key               ← Vault Secret, is_folder=0
    └── sendgrid_api_key         ← Vault Secret, is_folder=0
```

When a secret is created at `apps/myapp/stripe_key`, the parent folder documents `apps` and `apps/myapp` are created automatically if they do not already exist.

### Permission Model

| User type | Access granted by | Scope |
|-----------|-------------------|-------|
| Administrator | Built-in | All secrets and folders |
| System Manager (or role in `vault_allowed_roles`) | Role | Full proxy API; can list/read all secrets via `vault_proxy` endpoints |
| Any Frappe user | DocShare on a specific `Vault Secret` | That secret only |
| Any Frappe user | DocShare on a folder `Vault Secret` | All secrets under that folder (recursive) |
| Any Frappe user | `if_owner` permission | Secrets they created |

> **Proxy vs Secrets API**: `vault_allowed_roles` controls the generic `/v1/*` proxy endpoints. The Vault Secrets API endpoints (`get_secret`, `create_secret`, etc.) use `Vault Secret` document permissions independently.

### Sharing a Folder

Sharing a folder grants access to all secrets that descend from it. The `has_permission` hook on `Vault Secret` walks the folder ancestry; if any ancestor folder is shared with the requesting user, access is granted.

```python
# bench console
from frappe_vault.frappe_vault import share_folder

# Share the "apps/myapp" folder with a developer
share_folder("apps/myapp", "developer@example.com", read=1, write=1, share=0)
# The developer can now read and write all secrets inside apps/myapp
```

You can also share from the Frappe UI: open a folder `Vault Secret` document and use the **Share** button.

### API Endpoints

All endpoints require `vault_secrets_api_enabled: true`.

| Endpoint | Description |
|----------|-------------|
| `frappe_vault.frappe_vault.get_secrets` | List secrets (folders excluded); filterable by `folder`, `tag`, `search` |
| `frappe_vault.frappe_vault.get_folders` | List folder names the current user can read |
| `frappe_vault.frappe_vault.get_secret` | Get secret metadata and effective permissions (no value) |
| `frappe_vault.frappe_vault.reveal_secret` | Fetch the actual secret value from OpenBao |
| `frappe_vault.frappe_vault.create_secret` | Create a new secret (auto-creates folder chain) |
| `frappe_vault.frappe_vault.update_secret` | Update a secret's value or metadata |
| `frappe_vault.frappe_vault.delete_secret` | Delete a secret and its OpenBao entry |
| `frappe_vault.frappe_vault.share_folder` | Share a folder document with a Frappe user |

### OpenBao Path Format (Secrets API)

Secrets created through the Vault Secrets API are stored at:

```
secret/data/frappe/{site}/{path}
```

where `{path}` is the `path` field of the `Vault Secret` document (e.g. `ci-cd/github/actions_token`). This differs from the path used for Frappe Password fields, which uses `{doctype}/{docname}/{fieldname}` segments.

The existing OpenBao policy (`secret/data/frappe/*`) already covers both path formats.

## Multi-Site Sync (Data Replication)

For active-active multi-site deployments using Galera (MariaDB) and KeyDB (Redis), Frappe Vault supports bidirectional OpenBao synchronization. Each site has its own local OpenBao instance, and secrets are replicated across all sites.

> **Replication vs. Failover**
>
> `vault_sync` is a *data replication* feature, not a high-availability failover mechanism.
> Every read and every synchronous write targets the **local** OpenBao instance.  If local
> OpenBao is unavailable, requests fail — remote nodes are write destinations, not
> fallback read sources.  To expose the outage early, Frappe Vault redirects the
> `/login` page to a maintenance screen when local OpenBao is unreachable (see
> [Login Page Behavior During Outages](#login-page-behavior-during-outages) below).
>
> True service availability requires keeping the local OpenBao instance healthy
> (static-seal auto-unseal, Supervisor `autorestart`, monitoring on `/v1/sys/health`).

### Architecture Overview

```
┌─────────────────────────────────────────┐
│ Site A                                  │
│  Frappe ──► OpenBao (local, sync)       │
│     │                                   │
│     └──► RQ jobs ──► Site B OpenBao     │
│                  └──► Site N OpenBao    │
└─────────────────────────────────────────┘
```

- **Reads**: Always from local OpenBao only
- **Writes**: Synchronous to local OpenBao, async fan-out to remotes via RQ jobs
- **Failures**: Remote write failures are retried 3 times, 60 seconds apart
- **Reconciliation**: Hourly bidirectional sync compares all nodes
- **Conflicts**: Last-write-wins based on KV v2 `updated_time` metadata

### Configuration

Add to each site's `site_config.json`:

```json
{
  "vault_url": "http://localhost:8200",
  "vault_token": "local-token",
  "vault_sync_enabled": true,
  "vault_remotes": [
    {
      "name": "site-b",
      "url": "https://site-b.example.com:8200",
      "token": "site-b-token"
    },
    {
      "name": "site-c",
      "url": "https://site-c.example.com:8200",
      "token": "site-c-token"
    }
  ]
}
```

Each site should list the OTHER sites as remotes (not itself).

### Remote Configuration Options

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `name` | string | Yes | Unique identifier for this remote (for logging) |
| `url` | string | Yes | Full URL to the remote OpenBao server |
| `token` | string | Yes | Authentication token for the remote |

### How Sync Works

1. **On Write**: When a secret is written via `sync_write()`:
   - Written synchronously to local OpenBao
   - Background RQ job enqueued for each remote
   - If remote is down, job retries 3x with 60s delay

2. **On Delete**: Same pattern - local delete, then async remote deletes

3. **Hourly Reconciliation** (runs at :39 past each hour):
   - Lists all secrets from local and each remote
   - Compares `updated_time` from KV v2 metadata
   - Syncs newer secrets in both directions
   - Logs conflicts and sync statistics

### Scheduler Considerations

In Frappe HA deployments, only one worker node runs the scheduler. The reconciliation job runs on whichever node has the scheduler enabled. Since KeyDB replicates the RQ job queue across sites, failed write retries will be processed regardless of which site's worker picks them up.

### Using Sync-Aware Write Functions

To take advantage of multi-site sync, use the sync-aware functions:

```python
from frappe_vault.vault_sync import sync_write, sync_delete

# Write with automatic replication
sync_write("User", "admin@example.com", "api_key", "secret-value")

# Delete with automatic replication
sync_delete("User", "admin@example.com", "api_key")
```

The original `VaultClient.set_secret()` and `delete_secret()` methods still work but only write to the local OpenBao instance.

### Manual Reconciliation

To trigger reconciliation manually:

```python
# bench console
from frappe_vault.vault_sync import reconcile_all

result = reconcile_all()
print(result)
# {
#   "status": "completed",
#   "started_at": "2026-01-17T10:39:00",
#   "remotes": {
#     "site-b": {"status": "completed", "pulled": 2, "pushed": 1, "skipped": 50},
#     "site-c": {"status": "completed", "pulled": 0, "pushed": 3, "skipped": 50}
#   },
#   "completed_at": "2026-01-17T10:39:05"
# }
```

### Troubleshooting Multi-Site Sync

**Check sync status:**
```python
from frappe_vault.vault_client import is_sync_enabled, get_remote_clients

print("Sync enabled:", is_sync_enabled())
print("Remotes configured:", list(get_remote_clients().keys()))
```

**Check remote connectivity:**
```python
from frappe_vault.vault_client import get_remote_clients

for name, client in get_remote_clients().items():
    print(f"{name}: {'available' if client.is_available() else 'unreachable'}")
```

**Check failed jobs:**
```shell
# View failed RQ jobs
bench --site {site} show-pending-jobs
```

## Login Page Behavior During Outages

When any vault feature is enabled (`vault_password_fields_enabled`, `enable_vault_user_passwords`, or `vault_secrets_api_enabled`)
and local OpenBao is unreachable, users who visit `/login` are automatically redirected to
`/vault-unavailable` — a maintenance page that explains the situation and provides a
**Try Again** link.

This redirect happens server-side before the login form is rendered, so users see a clear
message rather than a cryptic authentication error after submitting credentials.

The availability check is cached in Redis for 30 seconds per site, so:
- Normal operation: negligible overhead (cache hit on every login page load)
- Outage detected: maintenance page shown within one cache TTL of the failure
- Recovery: login page accessible again within 30 seconds of OpenBao coming back

**Note**: Programmatic callers hitting `/api/method/login` directly are unaffected by
the redirect and continue to receive `frappe.AuthenticationError` as before.

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

### setup-openbao

Full automated OpenBao setup — config files, process manager registration, initialisation, KV engine, and site config. Run once after installing the app.

```shell
# Development (adds to Procfile, starts OpenBao in background):
bench setup-openbao

# Production (writes supervisor config, starts via supervisorctl):
bench setup-openbao --production

# Specify a site to update (auto-detected when bench has exactly one site):
bench setup-openbao --site {site}
bench setup-openbao --production --site {site}
```

**What it does:**
1. Generates `config/openbao.hcl`, `config/openbao-seal.key`, and `config/openbao-data/`
2. **Dev**: adds `openbao: bench run-openbao` to the Procfile. **Production**: writes `/etc/supervisor/conf.d/openbao.conf` and reloads supervisor
3. Starts OpenBao and polls until healthy
4. Initialises OpenBao; saves recovery keys to `config/openbao-recovery-keys.txt` (0600)
5. Enables the KV v2 secrets engine at `secret/`
6. Writes `vault_url` and `vault_token` to `{site}/site_config.json` (feature flags are **not** enabled automatically)

If any step has already been completed (e.g. OpenBao is already running or already initialised) that step is skipped automatically.

### run-openbao

Start the OpenBao server process. This command is invoked by `bench start` via the Procfile entry added by `bench setup-openbao`. You do not need to call it directly.

```shell
bench run-openbao
```

### vault-status

Check OpenBao connectivity and configuration status.

```shell
bench --site {site} vault-status
```

**Output:**
- Site configuration (`vault_password_fields_enabled`, `enable_vault_user_passwords`, `vault_secrets_api_enabled`, `vault_proxy_enabled`)
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
