<!-- Copyright (c) 2025, AgriTheory and contributors
For license information, please see license.txt-->

# Frappe Cloud Deployment

<div class="byline">
  Tyler Matteson 2026-06-29
</div>

Frappe Cloud runs benches inside containers as an unprivileged user. The bench cannot install system packages, write to `/usr/local/bin`, or modify Supervisor configuration. Therefore **Frappe Vault cannot install or run OpenBao inside the Frappe Cloud bench**; you must provide an external OpenBao instance and point Frappe Vault at it.

## Architecture

```
┌─────────────────────┐         HTTPS          ┌──────────────────┐
│ Frappe Cloud bench  │  ───────────────────►  │ External OpenBao │
│  (frappe_vault)     │   BAO_TOKEN header     │  (self-hosted    │
└─────────────────────┘                        │   or managed)    │
                                               └──────────────────┘
```

## Prerequisites

1. A running OpenBao server reachable from the Frappe Cloud container over HTTPS.
2. KV v2 secrets engine enabled at `secret/`.
3. A token with a policy allowing Frappe Vault's path layout.

### Minimal OpenBao policy

Save as `frappe-vault-policy.hcl` on the OpenBao server:

```hcl
path "secret/data/frappe/*" {
  capabilities = ["create", "read", "update", "delete"]
}

path "secret/metadata/frappe/*" {
  capabilities = ["list", "delete"]
}
```

Apply it:

```shell
export BAO_ADDR='https://openbao.example.com:8200'
export BAO_TOKEN='bao.xxxxx'
bao policy write frappe-vault frappe-vault-policy.hcl
bao token create -policy=frappe-vault -period=768h -display-name="frappe-cloud"
```

## Install Frappe Vault on Frappe Cloud

The patched `install.py` detects the restricted container environment and skips the local OpenBao installation and Supervisor configuration steps.

Install the app as usual via the Frappe Cloud dashboard or:

```shell
bench --site {site} install-app frappe_vault
```

You will see warnings like:

```
Cannot install OpenBao locally (no root/sudo or non-interactive environment).
...
No root/sudo access; skipping supervisor configuration. On Frappe Cloud you must run OpenBao externally.
```

These are expected and the install will continue.

## Configure the connection

Add to the site's `site_config.json`:

```json
{
  "vault_url": "https://openbao.example.com:8200",
  "vault_token": "bao.xxxxx"
}
```

For better security, prefer environment variables (set via the Frappe Cloud dashboard if supported):

| Variable | Description |
|----------|-------------|
| `BAO_ADDR` | OpenBao server URL (overrides `vault_url`) |
| `BAO_TOKEN` | OpenBao token (overrides `vault_token`) |
| `VAULT_ADDR` | Legacy alias for `BAO_ADDR` |
| `VAULT_TOKEN` | Legacy alias for `BAO_TOKEN` |

## Enable Frappe Vault features

After the connection is configured, enable the features you need in `site_config.json`:

```json
{
  "vault_password_fields_enabled": true,
  "enable_vault_user_passwords": true,
  "vault_secrets_api_enabled": true
}
```

Restart the bench from Frappe Cloud so the changes take effect.

## Verify connectivity

Open a bench console:

```python
from frappe_vault.vault_client import get_vault_client
client = get_vault_client()
print("OpenBao available:", client.is_available())
print("Health:", client.check_health())
```

If this returns `False`, check:

- The `vault_url` / `BAO_ADDR` is correct and reachable from the bench container.
- The token is valid and the policy is attached.
- TLS certificates are trusted by the Frappe Cloud container.

## Migrate existing passwords

If the site already has passwords in the `__Auth` table, migrate them after enabling the vault features:

```shell
bench --site {site} migrate-passwords-to-vault --dry-run
bench --site {site} migrate-passwords-to-vault
```

## Important notes

- **No local fallback**: Frappe Vault does not silently fall back to `__Auth`. If the external OpenBao becomes unreachable, password reads/writes will fail and the login page will redirect to `/vault-unavailable`.
- **TLS**: Always use HTTPS for the external OpenBao endpoint. Do not expose OpenBao to the public internet; restrict access to the Frappe Cloud egress IPs or use a private network.
- **Token lifecycle**: Use a periodic token or configure token renewal. If the token expires, Frappe Vault will lose access to secrets.
- **Backup**: Keep a backup of the OpenBao seal key and recovery keys outside of Frappe Cloud.

## Troubleshooting

### Install still fails with a sudo error

Ensure you are using the patched `install.py` (version-15 branch with the Frappe Cloud compatibility changes). The function `can_install_openbao_locally()` should return `False` in the container and skip the local install.

### `VaultConnectionError` after install

- Confirm the OpenBao server is running: `curl -s https://openbao.example.com:8200/v1/sys/health`
- Verify the token works from your local machine:
  ```shell
  export BAO_ADDR='https://openbao.example.com:8200'
  export BAO_TOKEN='bao.xxxxx'
  bao token lookup
  ```
- Check Frappe Cloud firewall/egress rules allow outbound HTTPS to the OpenBao host.

### Cannot reach OpenBao from Frappe Cloud

Some Frappe Cloud plans restrict outbound traffic to specific ports/hosts. If your OpenBao server is behind a firewall, whitelist the Frappe Cloud bench egress IPs, or run OpenBao on a host that is already reachable from the container.
