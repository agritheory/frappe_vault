<!-- Copyright (c) 2025, AgriTheory and contributors
For license information, please see license.txt-->

# OpenBao Setup Guide

This guide covers setting up OpenBao for use with Frappe Vault.

## What is OpenBao?

[OpenBao](https://openbao.org) is an open-source fork of HashiCorp Vault, created after HashiCorp changed Vault's license from MPL-2.0 to BSL in 2023. It is governed by the [Open Source Security Foundation (OpenSSF)](https://openssf.org/) and maintains API compatibility with Vault OSS v1.14.x.

- **License**: MPL-2.0 (Mozilla Public License 2.0)
- **CLI Command**: `bao` (instead of `vault`)
- **Environment Variables**: `BAO_ADDR`, `BAO_TOKEN` (also supports legacy `VAULT_ADDR`, `VAULT_TOKEN`)

## Installation

### Ubuntu/Debian

```shell
# Add OpenBao GPG key
wget -O- https://apt.releases.openbao.org/gpg | sudo gpg --dearmor -o /usr/share/keyrings/openbao-archive-keyring.gpg

# Add repository
echo "deb [signed-by=/usr/share/keyrings/openbao-archive-keyring.gpg] https://apt.releases.openbao.org $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/openbao.list

# Install
sudo apt update && sudo apt install openbao
```

### macOS

```shell
brew install openbao/tap/openbao
```

### Other Platforms

See the [official OpenBao installation guide](https://openbao.org/docs/install).

## Development Setup

For development, run OpenBao in dev mode:

```shell
bao server -dev -dev-listen-address=127.0.0.1:8200
```

This will output:
- **Unseal Key**: Used to unseal OpenBao (not needed in dev mode)
- **Root Token**: Use this as your `BAO_TOKEN`

**Warning**: Dev mode is insecure and should never be used in production. All data is stored in memory and lost on restart.

## Production Setup

### 1. Create Configuration File

Create `/etc/openbao/config.hcl`:

```hcl
ui = true

storage "file" {
  path = "/opt/openbao/data"
}

listener "tcp" {
  address     = "127.0.0.1:8200"
  tls_disable = false
  tls_cert_file = "/etc/openbao/tls/openbao.crt"
  tls_key_file  = "/etc/openbao/tls/openbao.key"
}

api_addr = "https://127.0.0.1:8200"
cluster_addr = "https://127.0.0.1:8201"
```

### 2. Create Data Directory

```shell
sudo mkdir -p /opt/openbao/data
sudo chown openbao:openbao /opt/openbao/data
```

### 3. Configure TLS Certificates

Generate or obtain TLS certificates and place them in `/etc/openbao/tls/`.

For self-signed certificates (development only):
```shell
sudo mkdir -p /etc/openbao/tls
cd /etc/openbao/tls

# Generate CA
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days 365 -key ca.key -out ca.crt -subj "/CN=OpenBao CA"

# Generate server certificate
openssl genrsa -out openbao.key 4096
openssl req -new -key openbao.key -out openbao.csr -subj "/CN=localhost"
openssl x509 -req -days 365 -in openbao.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out openbao.crt

sudo chown openbao:openbao /etc/openbao/tls/*
sudo chmod 600 /etc/openbao/tls/openbao.key
```

### 4. Create Systemd Service

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

[Install]
WantedBy=multi-user.target
```

### 5. Start and Initialize OpenBao

```shell
# Start OpenBao
sudo systemctl enable openbao
sudo systemctl start openbao

# Set environment
export BAO_ADDR='https://127.0.0.1:8200'
export BAO_CACERT='/etc/openbao/tls/ca.crt'

# Initialize OpenBao (only once)
bao operator init -key-shares=5 -key-threshold=3

# IMPORTANT: Save the unseal keys and root token securely!

# Unseal OpenBao (required after every restart)
bao operator unseal  # Run 3 times with different keys
```

### 6. Enable KV Secrets Engine

```shell
export BAO_TOKEN='bao.xxxxx'  # Root token from init

# Enable KV v2 secrets engine
bao secrets enable -path=secret kv-v2
```

### 7. Create Frappe Policy

Create `frappe-vault-policy.hcl`:
```hcl
# Allow full access to frappe secrets
path "secret/data/frappe/*" {
  capabilities = ["create", "read", "update", "delete"]
}

path "secret/metadata/frappe/*" {
  capabilities = ["list", "delete"]
}
```

Apply and create token:
```shell
bao policy write frappe-vault frappe-vault-policy.hcl
bao token create -policy=frappe-vault -period=768h -display-name="frappe-app"
```

### 8. Enable Audit Logging

```shell
# File-based audit log
bao audit enable file file_path=/var/log/openbao/audit.log

# Or syslog
bao audit enable syslog tag="openbao" facility="AUTH"
```

## Supervisor Integration

For Frappe deployments using Supervisor, add OpenBao as a managed service.

Create `/etc/supervisor/conf.d/openbao.conf`:

```ini
[program:openbao]
command=/usr/bin/bao server -config=/etc/openbao/config.hcl
autostart=true
autorestart=true
user=openbao
stdout_logfile=/var/log/openbao/openbao.log
stderr_logfile=/var/log/openbao/openbao-error.log
environment=HOME="/etc/openbao"
```

**Note**: You'll still need to manually unseal OpenBao after a restart, or implement auto-unseal using a cloud KMS.

## Auto-Unseal (Optional)

For production environments, consider using auto-unseal with a cloud KMS:

- [AWS KMS](https://openbao.org/docs/configuration/seal/awskms)
- [GCP Cloud KMS](https://openbao.org/docs/configuration/seal/gcpckms)
- [Azure Key Vault](https://openbao.org/docs/configuration/seal/azurekeyvault)

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

### OpenBao is Sealed

After a restart, OpenBao must be unsealed:
```shell
bao operator unseal
# Enter unseal key (repeat 3 times with different keys)
```

### Connection Refused

Check OpenBao is running:
```shell
sudo systemctl status openbao
```

### Certificate Errors

For self-signed certificates, set:
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

## Migration from HashiCorp Vault

If you are migrating from HashiCorp Vault, OpenBao provides API compatibility with Vault OSS v1.14.x. Key differences:

- CLI command: `bao` instead of `vault`
- Environment variables: `BAO_ADDR`, `BAO_TOKEN` (legacy `VAULT_*` still supported)
- Token format: OpenBao tokens start with `bao.` instead of `hvs.`

See the [OpenBao migration guide](https://openbao.org/docs/guides/migration) for detailed instructions.
