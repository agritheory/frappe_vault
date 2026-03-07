<!-- Copyright (c) 2025, AgriTheory and contributors
For license information, please see license.txt-->

# OpenBao Setup Guide

<div class="byline">
  Tyler Matteson 2026-01-17
</div>


This guide covers setting up OpenBao for use with Frappe Vault.

## What is OpenBao?

[OpenBao](https://openbao.org) is an open-source fork of HashiCorp Vault, created after HashiCorp changed Vault's license from MPL-2.0 to BSL in 2023. It is governed by the [Open Source Security Foundation (OpenSSF)](https://openssf.org/) and maintains API compatibility with Vault OSS v1.14.x.

- **License**: MPL-2.0 (Mozilla Public License 2.0)
- **CLI Command**: `bao` (instead of `vault`)
- **Environment Variables**: `BAO_ADDR`, `BAO_TOKEN` (also supports legacy `VAULT_ADDR`, `VAULT_TOKEN`)

## Installation

See the [official OpenBao installation guide](https://openbao.org/docs/install) for the latest instructions.

### macOS (Homebrew)

```shell
brew install openbao
```

### FreeBSD

```shell
pkg install openbao
```

### Linux (Manual Download)

OpenBao does not yet have a Linux package repository. Download packages manually from the [GitHub releases page](https://github.com/openbao/openbao/releases):

```shell
# Example for Ubuntu/Debian (amd64)
VERSION="2.4.4"  # Check for latest version
wget https://github.com/openbao/openbao/releases/download/v${VERSION}/bao-hsm_${VERSION}_linux_amd64.deb
sudo dpkg -i bao-hsm_${VERSION}_linux_amd64.deb
```

### Container Images

OpenBao provides pre-built container images:

```shell
# Alpine-based (recommended)
docker pull quay.io/openbao/openbao:latest
# or
docker pull ghcr.io/openbao/openbao:latest
# or
docker pull docker.io/openbao/openbao:latest

# RHEL UBI-based
docker pull quay.io/openbao/openbao-ubi:latest
```

### Precompiled Binaries

Download the appropriate binary for your system from the [releases page](https://github.com/openbao/openbao/releases), unzip, and place the `bao` binary on your `PATH`.

## Development Setup

For development, use the automated setup command:

```shell
bench setup-openbao
```

This interactive command will:
1. Create OpenBao configuration with static seal (auto-unseal on restart)
2. Add `bench run-openbao` to your Procfile
3. Configure audit logging

Then start your bench:
```shell
bench start
```

On first start, OpenBao will automatically:
- Initialize itself
- Save the root token to your site config(s)
- Enable the kv-v2 secrets engine
- Display the recovery key (save this somewhere safe)

**No manual configuration required!** The token is automatically saved to both `common_site_config.json` and any site-specific configs.

### Resetting OpenBao

To start fresh (deletes all secrets):
```shell
bench remove-openbao --confirm
bench setup-openbao
bench start
```

### Legacy Dev Mode

You can still run OpenBao in dev mode for quick testing, but data is lost on restart:

```shell
bao server -dev -dev-listen-address=127.0.0.1:8200
```

This outputs a root token to use in your site config. **Warning**: Dev mode should never be used in production.

## Production Setup

OpenBao v2.4+ supports **static seal auto-unseal**, which allows OpenBao to automatically unseal on startup using a symmetric AES-256 key. This is the recommended configuration for Frappe deployments managed by Supervisor, as it ensures OpenBao remains available after `bench restart`.

### Quick Setup (Recommended)

The easiest way to set up OpenBao for production is to use the Frappe Vault CLI:

```shell
bench setup-openbao --production
```

This creates:
- `config/openbao.hcl` - OpenBao configuration file with auto-unseal and audit logging
- `config/openbao-seal.key` - Static seal key for auto-unseal
- `config/openbao-data/` - Data storage directory
- `/etc/supervisor/conf.d/openbao.conf` - Supervisor configuration (if permissions allow)

The command outputs the next steps for initialization. After starting OpenBao via supervisor, you'll need to initialize it once and enable the secrets engine.

### Manual Setup

If you prefer to configure OpenBao manually or need a system-wide installation:

#### 1. Generate Seal Key

Generate a 32-byte (256-bit) seal key:

```shell
# Generate a 64-character hex key (32 bytes)
openssl rand -hex 32
```

Alternatively, `bench setup-openbao --manual` will generate config files without configuring a process manager.

#### 2. Choose Configuration Location

**Option A: Bench Config Directory (Recommended)**

Store OpenBao config alongside your bench. No sudo required.

- Config: `~/frappe-bench/config/openbao.hcl`
- Seal key: `~/frappe-bench/config/openbao-seal.key`
- Data: `~/frappe-bench/config/openbao-data/`

**Option B: System Directory**

Traditional system-wide installation requiring root access.

- Config: `/etc/openbao/config.hcl`
- Seal key: `/etc/openbao/seal.key`
- Data: `/opt/openbao/data/`

#### 3. Create Configuration File

Example for bench config directory (`~/frappe-bench/config/openbao.hcl`):

```hcl
ui = true

log_level = "info"
log_format = "standard"

storage "file" {
  path = "/home/frappe/frappe-bench/config/openbao-data"
}

listener "tcp" {
  address     = "127.0.0.1:8200"
  tls_disable = true  # TLS handled by nginx reverse proxy
}

# Static seal for auto-unseal on restart
seal "static" {
  current_key_id = "frappe-vault-1"
  current_key = "file:///home/frappe/frappe-bench/config/openbao-seal.key"
}

# Audit logging - all requests/responses are logged
audit_device "file" {
  path      = "file"
  file_path = "/home/frappe/frappe-bench/logs/openbao-audit.log"
}

api_addr = "http://127.0.0.1:8200"
```

Create the seal key file:
```shell
# Replace with your generated key
echo "YOUR_64_CHAR_HEX_KEY_HERE" > ~/frappe-bench/config/openbao-seal.key
chmod 600 ~/frappe-bench/config/openbao-seal.key
```

#### 4. Create Data Directory

```shell
mkdir -p ~/frappe-bench/config/openbao-data
```

### 5. Create Systemd Service (for systemd deployments)

Create `/etc/systemd/system/openbao.service`:

```ini
[Unit]
Description=OpenBao
Documentation=https://openbao.org/docs/
Requires=network-online.target
After=network-online.target
ConditionFileNotEmpty=/etc/openbao/config.hcl

[Service]
User=openbao
Group=openbao
ProtectSystem=full
ProtectHome=read-only
PrivateTmp=yes
PrivateDevices=yes
SecureBits=keep-caps
AmbientCapabilities=CAP_IPC_LOCK
NoNewPrivileges=yes
ExecStart=/usr/bin/bao server -config=/etc/openbao/config.hcl
ExecReload=/bin/kill --signal HUP $MAINPID
KillMode=process
KillSignal=SIGINT
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
LimitNOFILE=65536
LimitMEMLOCK=infinity
# For env-based seal key (Option A with systemd):
# Environment=BAO_SEAL_KEY=YOUR_64_CHAR_HEX_KEY_HERE

[Install]
WantedBy=multi-user.target
```

### 6. Start and Initialize OpenBao

```shell
# Start OpenBao
sudo systemctl enable openbao
sudo systemctl start openbao

# Set environment
export BAO_ADDR='http://127.0.0.1:8200'

# Initialize OpenBao (only once)
# With static seal, this generates recovery keys instead of unseal keys
bao operator init -recovery-shares=5 -recovery-threshold=3

# IMPORTANT: Save the recovery keys and root token securely!
# Recovery keys are for emergency access, not routine restarts.
# OpenBao will auto-unseal using the static seal key.
```

**Note**: With static seal configured, OpenBao automatically unseals on startup. The recovery keys are only needed for emergency situations (e.g., seal key rotation, disaster recovery).

### 7. Enable KV Secrets Engine

```shell
export BAO_TOKEN='bao.xxxxx'  # Root token from init

# Enable KV v2 secrets engine
bao secrets enable -path=secret kv-v2
```

### 8. Create Frappe Policy

Create `frappe-vault-policy.hcl`:
```hcl
# Allow full access to frappe secrets for all sites
# Secrets are stored at: secret/data/frappe/{site}/{doctype}/{name}/{fieldname}
# Example: secret/data/frappe/mysite.example.com/User/Administrator/password
path "secret/data/frappe/*" {
  capabilities = ["create", "read", "update", "delete"]
}

path "secret/metadata/frappe/*" {
  capabilities = ["list", "delete"]
}
```

For multi-tenant deployments, you can create site-specific policies:
```hcl
# Policy for site1.example.com only
path "secret/data/frappe/site1.example.com/*" {
  capabilities = ["create", "read", "update", "delete"]
}

path "secret/metadata/frappe/site1.example.com/*" {
  capabilities = ["list", "delete"]
}
```

Apply and create token:
```shell
bao policy write frappe-vault frappe-vault-policy.hcl
bao token create -policy=frappe-vault -period=768h -display-name="frappe-app"
```

### 9. Audit Logging

When using `bench setup-openbao`, audit logging is automatically configured in the HCL config file. Logs are written to `logs/openbao-audit.log` in your bench directory.

For manual setups, add the audit device to your `openbao.hcl`:
```hcl
audit_device "file" {
  path      = "file"
  file_path = "/var/log/openbao/audit.log"
}
```

**Note**: OpenBao 2.4+ requires audit devices to be configured declaratively in the config file, not via the API.

## Supervisor Integration

For Frappe deployments using Supervisor (recommended), add OpenBao as a managed service.

Create `/etc/supervisor/conf.d/openbao.conf`:

**For bench config directory setup (recommended):**
```ini
[program:openbao]
command=/usr/bin/bao server -config=/home/frappe/frappe-bench/config/openbao.hcl
autostart=true
autorestart=true
directory=/home/frappe/frappe-bench
stdout_logfile=/var/log/openbao/openbao.log
stderr_logfile=/var/log/openbao/openbao-error.log
```

**For system directory setup with env-based seal key:**
```ini
[program:openbao]
command=/usr/bin/bao server -config=/etc/openbao/config.hcl
autostart=true
autorestart=true
user=openbao
stdout_logfile=/var/log/openbao/openbao.log
stderr_logfile=/var/log/openbao/openbao-error.log
environment=HOME="/etc/openbao",BAO_SEAL_KEY="YOUR_64_CHAR_HEX_KEY_HERE"
```

With static seal configured:
- `bench restart` will restart OpenBao and it will **automatically unseal**
- No manual intervention required after restarts
- Recovery keys are only needed for emergencies

Create log directory and reload supervisor:
```shell
sudo mkdir -p /var/log/openbao
sudo supervisorctl reread
sudo supervisorctl update
```

## Seal Key Storage

Choose the storage method that best fits your security requirements:

### Environment Variable (`env://`)

```hcl
seal "static" {
  current_key_id = "frappe-vault-1"
  current_key = "env://BAO_SEAL_KEY"
}
```

**Pros:**
- Key not persisted as a separate file on disk
- Integrates well with container orchestration (K8s Secrets, Docker Secrets)
- Easy to manage with Supervisor

**Cons:**
- Visible to root via `/proc/<pid>/environ`
- Supervisor config file must be protected (0600 permissions)

### File-Based (`file://`)

```hcl
seal "static" {
  current_key_id = "frappe-vault-1"
  current_key = "file:///etc/openbao/seal.key"
}
```

**Pros:**
- Simpler setup for traditional deployments
- Easier to audit file access
- Works without modifying supervisor config

**Cons:**
- Key persists on disk (must secure with permissions)
- Must ensure file permissions are correct (0600, owned by openbao)

### Security Notes

Both methods require trusting the host system. Anyone with root access to the host can access the seal key. For high-security environments, consider:

- [AWS KMS auto-unseal](https://openbao.org/docs/configuration/seal/awskms)
- [GCP Cloud KMS auto-unseal](https://openbao.org/docs/configuration/seal/gcpckms)
- [Azure Key Vault auto-unseal](https://openbao.org/docs/configuration/seal/azurekeyvault)

## Seal Key Rotation

To rotate the seal key:

1. Generate a new key: `openssl rand -hex 32`
2. Update config with both keys:

```hcl
seal "static" {
  current_key_id = "frappe-vault-2"
  current_key = "env://BAO_SEAL_KEY"
  previous_key_id = "frappe-vault-1"
  previous_key = "env://BAO_SEAL_KEY_OLD"
}
```

3. Update environment with both keys
4. Restart OpenBao - it will re-wrap data with the new key
5. After confirming success, remove `previous_key` entries

## Alternative: Manual Unsealing (Shamir)

If you prefer manual unsealing with Shamir secret sharing (not recommended for Supervisor deployments), omit the `seal "static"` block from your configuration:

```hcl
ui = true

storage "file" {
  path = "/opt/openbao/data"
}

listener "tcp" {
  address     = "127.0.0.1:8200"
  tls_disable = true
}

api_addr = "http://127.0.0.1:8200"
```

Initialize with unseal keys:
```shell
bao operator init -key-shares=5 -key-threshold=3
```

After every restart, you must manually unseal:
```shell
bao operator unseal  # Run 3 times with different keys
```

**Warning**: With Shamir unsealing, `bench restart` will leave OpenBao sealed until manually unsealed. This breaks automated deployments and requires human intervention.

## Health Checks

Verify OpenBao is running correctly:

```shell
# Check health (no auth required)
curl -s https://localhost:8200/v1/sys/health | jq .

# Expected response for healthy, unsealed OpenBao:
{
  "initialized": true,
  "sealed": false,
  "standby": false,
  "performance_standby": false,
  "replication_performance_mode": "disabled",
  "replication_dr_mode": "disabled",
  "server_time_utc": 1234567890,
  "version": "2.0.0"
}
```

## Troubleshooting

### OpenBao is Sealed After Restart

If OpenBao is sealed after restart with static seal configured:

1. **Check seal key availability**:
   - For `env://`: Verify `BAO_SEAL_KEY` is set in supervisor/systemd config
   - For `file://`: Verify the key file exists and has correct permissions

2. **Check OpenBao logs**:
   ```shell
   sudo journalctl -u openbao -f
   # Or for supervisor:
   tail -f /var/log/openbao/openbao-error.log
   ```

3. **Verify key format**: The key must be exactly 64 hex characters (32 bytes)

4. **Emergency unseal with recovery keys** (if static seal fails):
   ```shell
   bao operator unseal -recovery
   # Enter recovery key (repeat as needed based on threshold)
   ```

### OpenBao is Sealed (Manual Shamir Setup)

If using manual Shamir unsealing (not recommended):
```shell
bao operator unseal
# Enter unseal key (repeat 3 times with different keys)
```

### Connection Refused

Check OpenBao is running:
```shell
sudo systemctl status openbao
# Or for supervisor:
sudo supervisorctl status openbao
```

### Certificate Errors

If using TLS (not via nginx proxy):
```shell
export BAO_SKIP_VERIFY=true
# Or
export BAO_CACERT=/path/to/ca.crt
```

### Permission Denied

Verify token has correct policy:
```shell
bao token lookup
```

### Seal Key File Permissions

If using file-based seal key:
```shell
ls -la /etc/openbao/seal.key
# Should show: -rw------- 1 openbao openbao
# Fix if needed:
sudo chmod 600 /etc/openbao/seal.key
sudo chown openbao:openbao /etc/openbao/seal.key
```

## Migration from HashiCorp Vault

If you are migrating from HashiCorp Vault, OpenBao provides API compatibility with Vault OSS v1.14.x. Key differences:

- CLI command: `bao` instead of `vault`
- Environment variables: `BAO_ADDR`, `BAO_TOKEN` (legacy `VAULT_*` still supported)
- Token format: OpenBao tokens start with `bao.` instead of `hvs.`

See the [OpenBao migration guide](https://openbao.org/docs/guides/migration) for detailed instructions.
