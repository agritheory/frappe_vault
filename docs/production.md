<!-- Copyright (c) 2025, AgriTheory and contributors
For license information, please see license.txt-->

# Frappe Vault Production Setup

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

5. **Configure OpenBao settings** in `site_config.json`:
```json
{
  "enable_vault_secrets": true,
  "enable_vault_user_passwords": true,
  "vault_url": "http://localhost:8200"
}
```

6. **Configure supervisor** with OpenBao token environment variable:

Edit your supervisor configuration (usually `/etc/supervisor/conf.d/frappe-bench.conf`):
```ini
[program:frappe-bench-frappe-web]
environment=BAO_TOKEN="bao.xxxxxxxxxxxxx"

[program:frappe-bench-frappe-worker-default]
environment=BAO_TOKEN="bao.xxxxxxxxxxxxx"

[program:frappe-bench-frappe-worker-short]
environment=BAO_TOKEN="bao.xxxxxxxxxxxxx"

[program:frappe-bench-frappe-worker-long]
environment=BAO_TOKEN="bao.xxxxxxxxxxxxx"

[program:frappe-bench-frappe-schedule]
environment=BAO_TOKEN="bao.xxxxxxxxxxxxx"
```

**Note**: Legacy `VAULT_TOKEN` environment variable is also supported for backward compatibility.

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
- [ ] OpenBao token is provided via environment variable, not site_config
- [ ] OpenBao audit logging is enabled
- [ ] OpenBao token has minimal required permissions (see below)
- [ ] External access uses Frappe proxy API (TLS via nginx)

## OpenBao Token Policy

Create a restricted policy for the Frappe application:

```hcl
# frappe-vault-policy.hcl
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
- Ensure OpenBao unseal keys are securely stored
- Test recovery procedures regularly

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
