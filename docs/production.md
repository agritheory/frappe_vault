<!-- Copyright (c) 2025, AgriTheory and contributors
For license information, please see license.txt-->

# Frappe Vault Production Setup

<div class="byline">
  Tyler Matteson 2026-03-07
</div>


Before you begin, ensure your server meets the following requirements:
- Python 3.10+ for Frappe version 15
- OpenBao installed and configured (see [OpenBao Setup Guide](./openbao-setup.md))

## Installation

1. **Set up a new bench**:
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
bench get-app frappe_vault git@github.com:agritheory/frappe_vault.git
```

4. **Install the app to your site**:
```shell
bench --site {{ site name }} install-app frappe_vault

# Optional: Verify installation
bench --site {{ site name }} list-apps
```

5. **Set up OpenBao**:
```shell
bench setup-openbao --production --site {{ site name }}
```

`bench setup-openbao` handles the full setup in one command:
- Generates `config/openbao.hcl` (static auto-unseal), `config/openbao-seal.key`, and `config/openbao-data/`
- Writes `/etc/supervisor/conf.d/openbao.conf` and reloads supervisor (uses `sudo tee` if not running as root)
- Starts OpenBao via supervisor and waits for it to be ready
- Initialises OpenBao and saves recovery keys to `config/openbao-recovery-keys.txt` (0600)
- Enables the KV v2 secrets engine at `secret/`
- Writes `vault_url`, `vault_token`, and `enable_vault_secrets: true` to `{{ site name }}/site_config.json`

> **Security note**: `setup-openbao` writes the root token directly to `site_config.json` for convenience. For production, replace it with a restricted policy token once the site is running — see [OpenBao Token Policy](#openbao-token-policy) below. You can also supply the token via environment variable instead:
> ```ini
> [program:frappe-bench-frappe-web]
> environment=BAO_TOKEN="bao.xxxxxxxxxxxxx"
> ```

6. **Set the admin password** (will be stored in OpenBao):
```shell
bench --site {{ site name }} set-admin-password {{ secure password }}
```

## Security Checklist

- [ ] OpenBao is bound to localhost only (`127.0.0.1:8200`)
- [ ] Static seal is configured for auto-unseal (see [OpenBao Setup Guide](./openbao-setup.md))
- [ ] Seal key is protected (env var in supervisor config, or file with 0600 permissions)
- [ ] OpenBao token is provided via environment variable, not site_config
- [ ] OpenBao audit logging is enabled
- [ ] OpenBao token has minimal required permissions (see below)
- [ ] Recovery keys are stored securely offline (for emergencies only)
- [ ] External access uses Frappe proxy API (TLS via nginx)

## OpenBao Token Policy

Create a restricted policy for the Frappe application:

```hcl
# frappe-vault-policy.hcl
# Secrets are namespaced by site: secret/data/frappe/{site}/{doctype}/{name}/{fieldname}
# This policy allows access to all sites - for multi-tenant isolation, create site-specific policies
path "secret/data/frappe/*" {
  capabilities = ["create", "read", "update", "delete"]
}

path "secret/metadata/frappe/*" {
  capabilities = ["list", "delete"]
}
```

Apply the policy and create a token:
```shell
bao policy write frappe-vault frappe-vault-policy.hcl
bao token create -policy=frappe-vault -period=24h
```

## Monitoring

### Health Check

Add a health check endpoint to your monitoring:
```shell
curl -s http://localhost:8200/v1/sys/health | jq .
```

### Audit Log Monitoring

Ensure OpenBao audit logs are being collected and monitored:
```shell
bao audit list
```

## Backup Considerations

- OpenBao data should be backed up separately from Frappe database
- **Seal key**: If using file-based seal key (`file://`), include `/etc/openbao/seal.key` in backups
- **Recovery keys**: Store recovery keys securely offline (encrypted, physically secure location)
  - Recovery keys are for emergencies only (seal key loss, disaster recovery)
  - With static seal, recovery keys are NOT needed for routine restarts
- Test recovery procedures regularly

### Seal Key Security Comparison

| Storage Method | Backup Needed | Notes |
|----------------|---------------|-------|
| Environment variable (`env://`) | Supervisor config | Key in supervisor config, protect with 0600 permissions |
| File-based (`file://`) | `/etc/openbao/seal.key` | Separate file, protect with 0600 permissions |

**Important**: Anyone with access to the seal key can unseal OpenBao. Treat the seal key with the same security as the secrets it protects.

## Troubleshooting

### OpenBao Connection Errors

Check connectivity:
```shell
curl -s -H "X-Vault-Token: ${BAO_TOKEN}" http://localhost:8200/v1/sys/health
```

### Authentication Failures

Verify token is valid:
```shell
bao token lookup
```

### Missing Passwords After Migration

Check if password exists in either location:
```python
# bench console
from frappe_vault.vault_client import get_vault_client

# Check database
Auth = frappe.qb.Table("__Auth")
db_result = (
    frappe.qb.from_(Auth)
    .select(Auth.star)
    .where(Auth.name == "Administrator")
    .run(as_dict=True)
)
print("In DB:", db_result)

# Check OpenBao
client = get_vault_client()
vault_result = client.get_secret("User", "Administrator", "password")
print("In OpenBao:", "Yes" if vault_result else "No")
```
