<!-- Copyright (c) 2025, AgriTheory and contributors
For license information, please see license.txt-->

# OpenBao on Ubuntu VPS

<div class="byline">
  Francisco Roldán 2026-06-30
</div>

This guide walks through installing a single-node OpenBao server on an Ubuntu VPS and connecting it to a Frappe Cloud site running Frappe Vault.

For the Frappe Cloud side of the configuration, see [Frappe Cloud Deployment](./frappe-cloud.md).

---

## Prerequisites

- A clean Ubuntu 22.04 or 24.04 LTS VPS.
- At least 2 GB RAM and a stable network connection.
- A domain or hostname pointing to the VPS (for example, `openbao.example.com`).
- The VPS must be reachable from the Frappe Cloud bench container over HTTPS.
- Root or sudo access on the VPS.

If the domain uses Cloudflare, set the OpenBao hostname to **DNS only** (grey cloud). Cloudflare proxies only standard HTTP/HTTPS ports (80/443), so requests to port `8200` will time out when the proxy is enabled.

---

## Prepare the VPS

Update the system and install required utilities:

```shell
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl gnupg lsb-release software-properties-common unzip wget
```

---

## Install OpenBao

Download the latest OpenBao binary from GitHub. Replace `2.5.5` with the current version if needed.

```shell
OPENBAO_VERSION="2.5.5"
wget https://github.com/openbao/openbao/releases/download/v${OPENBAO_VERSION}/bao_${OPENBAO_VERSION}_Linux_x86_64.tar.gz
tar -xzf bao_${OPENBAO_VERSION}_Linux_x86_64.tar.gz
sudo mv bao /usr/local/bin/
sudo chmod +x /usr/local/bin/bao
bao version
```

---

## Configure OpenBao

Create a dedicated system user and directories:

```shell
sudo useradd --system --home /opt/openbao --shell /bin/false openbao
sudo mkdir -p /opt/openbao/data /etc/openbao /etc/openbao/tls
sudo chown -R openbao:openbao /opt/openbao /etc/openbao
```

### TLS certificate

For production, use a certificate from Let's Encrypt or your certificate authority. A self-signed certificate is acceptable only for local testing.

#### Option A: Let's Encrypt (recommended)

Open port 80 for validation and obtain a certificate:

```shell
sudo ufw allow 80/tcp
sudo apt install -y certbot
sudo certbot certonly --standalone -d openbao.example.com
```

Create a renewal hook that copies the certificate into OpenBao's TLS directory and restarts the service:

```shell
sudo mkdir -p /etc/letsencrypt/renewal-hooks/deploy
sudo tee /etc/letsencrypt/renewal-hooks/deploy/openbao.sh << 'EOF'
#!/bin/bash
DOMAIN="openbao.example.com"
cp "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" /etc/openbao/tls/openbao.crt
cp "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" /etc/openbao/tls/openbao.key
chown -R openbao:openbao /etc/openbao/tls
systemctl restart openbao
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/openbao.sh
sudo /etc/letsencrypt/renewal-hooks/deploy/openbao.sh
```

#### Option B: Self-signed certificate (testing only)

```shell
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/openbao/tls/openbao.key \
  -out /etc/openbao/tls/openbao.crt \
  -subj "/CN=openbao.example.com"
sudo chown -R openbao:openbao /etc/openbao/tls
```

Browsers and Frappe Vault will reject a self-signed certificate unless it is explicitly trusted.

### OpenBao configuration file

Create `/etc/openbao/bao.hcl`:

```hcl
storage "file" {
  path = "/opt/openbao/data"
}

listener "tcp" {
  address       = "0.0.0.0:8200"
  tls_cert_file = "/etc/openbao/tls/openbao.crt"
  tls_key_file  = "/etc/openbao/tls/openbao.key"
}

api_addr     = "https://openbao.example.com:8200"
cluster_addr = "https://openbao.example.com:8201"
ui           = true
```

---

## Run OpenBao as a Service

Create `/etc/systemd/system/openbao.service`:

```ini
[Unit]
Description=OpenBao
Requires=network-online.target
After=network-online.target

[Service]
User=openbao
Group=openbao
ExecStart=/usr/local/bin/bao server -config=/etc/openbao/bao.hcl
ExecReload=/bin/kill --signal HUP $MAINPID
KillMode=process
Restart=on-failure
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

Reload systemd and start OpenBao:

```shell
sudo systemctl daemon-reload
sudo systemctl enable --now openbao
sudo systemctl status openbao
```

---

## Initialize and Unseal OpenBao

Set the server address and initialize OpenBao:

```shell
export BAO_ADDR='https://openbao.example.com:8200'
bao operator init -key-shares=5 -key-threshold=3
```

> **Note:** If you connect by IP address instead of the hostname, TLS verification will fail unless the certificate includes the IP as a subject alternative name. For the initialization step only, you can skip verification with `-tls-skip-verify` or `export BAO_SKIP_VERIFY=true`. For production, use the hostname or a certificate with the correct SANs.

The command prints **unseal keys** and a **root token**. Save these in a secure location outside the VPS.

Unseal OpenBao by running the unseal command three times with three different keys:

```shell
bao operator unseal
bao operator unseal
bao operator unseal
```

Check the status:

```shell
bao status
```

> **Note:** OpenBao seals again after a reboot or service restart unless you configure auto-unseal or a startup script that runs the unseal commands automatically. This is especially important with Let's Encrypt, because cert renewal hooks that restart OpenBao will leave it sealed until it is unsealed again.

---

## Enable KV v2 Secrets Engine

Authenticate with the root token and enable the KV v2 secrets engine:

```shell
export BAO_ADDR='https://openbao.example.com:8200'
export BAO_TOKEN='hvs.xxxxx'  # replace with your root token

bao secrets enable -path=secret kv-v2
```

---

## Create the Frappe Vault Policy and Token

Create `/etc/openbao/frappe-vault-policy.hcl`:

```hcl
path "secret/data/frappe/*" {
  capabilities = ["create", "read", "update", "delete"]
}

path "secret/metadata/frappe/*" {
  capabilities = ["list", "delete"]
}
```

Apply the policy and create a periodic token for Frappe Cloud:

```shell
export BAO_ADDR='https://openbao.example.com:8200'
export BAO_TOKEN='hvs.xxxxx'

bao policy write frappe-vault /etc/openbao/frappe-vault-policy.hcl
bao token create -policy=frappe-vault -period=768h -display-name="frappe-cloud"
```

Copy the generated token value (it usually starts with `bao.` or `hvs.`). This token is used in the Frappe Cloud configuration.

---

## Network and Security Hardening

- **Always use HTTPS** for the OpenBao endpoint.
- **Restrict access** to OpenBao. If possible, allow only Frappe Cloud egress IPs on port `8200`.
- **Do not expose OpenBao to the public internet** unless absolutely necessary. Prefer a private network or VPN between Frappe Cloud and the VPS.
- **Back up the unseal keys and root token** outside of the VPS and Frappe Cloud.
- **Use a firewall** such as UFW to limit access:

```shell
sudo ufw default deny incoming
sudo ufw allow 22/tcp
sudo ufw allow from <frappe-cloud-egress-ip> to any port 8200 proto tcp
sudo ufw enable
```

- Consider setting up **auto-unseal** with a cloud KMS or a trusted unseal partner to avoid manual unseal after reboots.

---

## Configure Frappe Cloud

Frappe Cloud runs benches in containers without root privileges, so OpenBao cannot be installed locally. Point Frappe Vault to the external OpenBao server instead.

See [Frappe Cloud Deployment](./frappe-cloud.md) for the full configuration steps, including:

- Setting `vault_url` and `vault_token` in `site_config.json`
- Using environment variables for better security
- Enabling `vault_password_fields_enabled`, `enable_vault_user_passwords`, and `vault_secrets_api_enabled`
- Verifying connectivity from the bench console

---

## Verify Connectivity

Open a bench console on Frappe Cloud:

```python
from frappe_vault.vault_client import get_vault_client
client = get_vault_client()
print("OpenBao available:", client.is_available())
print("Health:", client.check_health())
```

Expected output:

```text
OpenBao available: True
Health: {'status': 'ok', ... }
```

If it returns `False` or raises an error, check the troubleshooting steps below.

---

## Troubleshooting

### OpenBao is not reachable

From the Frappe Cloud bench or a machine with similar network access, run:

```shell
curl -s https://openbao.example.com:8200/v1/sys/health
```

If this fails, check:

- OpenBao is running: `sudo systemctl status openbao`
- The DNS record points to the VPS IP.
- The firewall allows inbound HTTPS on port `8200`.
- TLS certificates are valid and not expired.
- **Cloudflare proxy:** If the domain uses Cloudflare, disable the orange-cloud proxy for the OpenBao hostname. Cloudflare only proxies standard HTTP/HTTPS ports (80/443), so requests to port `8200` will time out while the proxy is enabled. Set the DNS record to **DNS only** (grey cloud) so `openbao.example.com` resolves directly to the VPS IP.

### Certificate errors in browser or Frappe Vault

If you see `NET::ERR_CERT_AUTHORITY_INVALID` in the browser or `Secret management service unavailable` in Frappe Cloud, OpenBao is likely using a self-signed certificate. Browsers and the Frappe Vault client require a publicly trusted certificate.

**Fix:** Use Let's Encrypt as shown in the [Configure OpenBao](#configure-openbao) section. After installing a valid certificate for `openbao.example.com`, restart OpenBao and ensure `vault_url` in `site_config.json` uses the same hostname.

### Token is invalid

From your local machine or the VPS:

```shell
export BAO_ADDR='https://openbao.example.com:8200'
export BAO_TOKEN='bao.xxxxx'
bao token lookup
```

If this fails, recreate the token and ensure the `frappe-vault` policy is attached.

### `VaultConnectionError` in Frappe Cloud

- Confirm the OpenBao server is running and unsealed.
- Verify the token is valid and has the correct policy.
- Check Frappe Cloud egress rules allow outbound HTTPS to your VPS.
- Ensure the TLS certificate is trusted by the Frappe Cloud container.

### OpenBao seals after reboot or service restart

OpenBao seals automatically on restart. You must unseal it manually or configure auto-unseal. For production deployments, use a supported auto-unseal method such as a cloud KMS. Remember that Let's Encrypt renewal hooks or any `systemctl restart openbao` will also reseal OpenBao.

---

## Summary

You now have a single-node OpenBao server running on Ubuntu, configured with a KV v2 secrets engine and a dedicated Frappe Vault policy. Frappe Cloud connects to this external server over HTTPS, storing and retrieving secrets without needing a local OpenBao installation.

Keep your unseal keys, root token, and Frappe Vault token secure and backed up outside of both the VPS and Frappe Cloud.
