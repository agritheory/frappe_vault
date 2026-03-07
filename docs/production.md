<!-- Copyright (c) 2025, AgriTheory and contributors
For license information, please see license.txt-->

# Frappe Vault Production Setup

<div class="byline">
  Tyler Matteson 2026-01-17
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

5. **Set up OpenBao for production**:
```shell
bench setup-openbao --production
```

This creates:
- OpenBao configuration with auto-unseal and audit logging
- Supervisor configuration for OpenBao (outputs to console if `/etc/supervisor/conf.d/` isn't writable)

6. **Reload supervisor and start OpenBao**:
```shell
sudo supervisorctl reread
sudo supervisorctl update
```

7. **Initialize OpenBao** (first time only):
```shell
export BAO_ADDR='http://127.0.0.1:8200'
bao operator init -recovery-shares=1 -recovery-threshold=1
```

**Save the recovery key and root token securely!**

8. **Enable the secrets engine**:
```shell
export BAO_TOKEN='<root-token-from-init>'
bao secrets enable -path=secret kv-v2
```

9. **Configure the Frappe token** in `site_config.json`:
```json
{
  "enable_vault_secrets": true,
  "enable_vault_user_passwords": true,
  "vault_url": "http://127.0.0.1:8200",
  "vault_token": "<root-token-or-policy-token>"
}
```

For better security, create a restricted policy token instead of using the root token. See [OpenBao Token Policy](#openbao-token-policy) below.

Alternatively, configure supervisor with the token as an environment variable:
```ini
[program:frappe-bench-frappe-web]
environment=BAO_TOKEN="bao.xxxxxxxxxxxxx"
```

See [OpenBao Setup Guide](./openbao-setup.md) for more configuration options.

If OpenBao is managed by supervisor with environment-based seal key, also configure the OpenBao program:
```ini
[program:openbao]
command=/usr/bin/bao server -config=/etc/openbao/config.hcl
autostart=true
autorestart=true
user=openbao
stdout_logfile=/var/log/openbao/openbao.log
stderr_logfile=/var/log/openbao/openbao-error.log
environment=HOME="/etc/openbao",BAO_SEAL_KEY="<64-char-hex-key>"
```

See [OpenBao Setup Guide](./openbao-setup.md) for generating the seal key and configuration options.

7. **Reload supervisor**:
```shell
sudo supervisorctl reread
sudo supervisorctl update
```

8. **Set the admin password**:
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
