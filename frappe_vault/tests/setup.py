# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
from frappe.desk.doctype.tag.tag import add_tag


def before_test(company_name=None):
	"""Initialize test environment with complete setup and fixture data."""
	frappe.clear_cache()
	today = frappe.utils.getdate()
	setup_complete(
		{
			"currency": "USD",
			"full_name": "Administrator",
			"company_name": "Test Company",
			"timezone": "America/New_York",
			"company_abbr": "TC",
			"domains": ["Distribution"],
			"country": "United States",
			"fy_start_date": today.replace(month=1, day=1).isoformat(),
			"fy_end_date": today.replace(month=12, day=31).isoformat(),
			"language": "english",
			"company_tagline": "Test Company",
			"email": "Administrator",
			"password": "admin",
			"chart_of_accounts": "Standard with Numbers",
			"bank_account": "Primary Checking",
		}
	)
	for modu in frappe.get_all("Module Onboarding"):
		frappe.db.set_value("Module Onboarding", modu, "is_complete", 1)
	frappe.set_value("Website Settings", "Website Settings", "home_page", "login")
	frappe.db.commit()
	create_test_data()


def create_test_data():
	"""Create comprehensive test data for vault secrets."""
	create_vault_secrets()


def create_vault_secrets():
	"""Create test secrets with folder hierarchy and various types.

	Demonstrates:
	- Folder nesting (apps/myapp, infrastructure/aws, etc.)
	- Single value secrets
	- Key-value pair secrets
	- Various tags
	- Different use cases
	"""
	secrets = [
		# Root level secrets
		{
			"title": "Master Encryption Key",
			"path": "master_key",
			"secret_type": "Single Value",
			"description": "Master encryption key for application",
			"tags": ["production"],
			"value": "mk_8a9f7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b",
		},
		{
			"title": "Admin API Token",
			"path": "admin_token",
			"secret_type": "Single Value",
			"description": "Administrator API access token",
			"tags": ["production", "api-key"],
			"value": "adm_tok_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
		},
		# Application secrets
		{
			"title": "MyApp Database Credentials",
			"path": "apps/myapp/database",
			"secret_type": "Key-Value Pairs",
			"description": "Database connection credentials for MyApp",
			"tags": ["production", "database"],
			"items": [
				{"key": "host", "value": "db.production.example.com"},
				{"key": "port", "value": "5432"},
				{"key": "username", "value": "myapp_user"},
				{"key": "password", "value": "MyS3cur3P@ssw0rd!2024"},
				{"key": "database", "value": "myapp_production"},
			],
		},
		{
			"title": "MyApp SendGrid API Key",
			"path": "apps/myapp/sendgrid",
			"secret_type": "Single Value",
			"description": "SendGrid API key for email delivery",
			"tags": ["production", "api-key", "smtp"],
			"value": "SG.FAKE_1234567890abcdefghijklmnopqrstuvwxyz.ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
		},
		{
			"title": "MyApp Stripe Credentials",
			"path": "apps/myapp/stripe",
			"secret_type": "Key-Value Pairs",
			"description": "Stripe payment processing credentials",
			"tags": ["production", "payment"],
			"items": [
				{"key": "publishable_key", "value": "pk_FAKE_51234567890abcdefghijklmnop"},
				{"key": "secret_key", "value": "sk_FAKE_51234567890abcdefghijklmnop"},
				{"key": "webhook_secret", "value": "whsec_FAKE_1234567890abcdefghijklmnop"},
			],
		},
		{
			"title": "MyApp OAuth GitHub",
			"path": "apps/myapp/oauth/github",
			"secret_type": "Key-Value Pairs",
			"description": "GitHub OAuth application credentials",
			"tags": ["production", "oauth"],
			"items": [
				{"key": "client_id", "value": "Iv1.a1b2c3d4e5f6g7h8"},
				{"key": "client_secret", "value": "1234567890abcdef1234567890abcdef12345678"},
			],
		},
		{
			"title": "MyApp OAuth Google",
			"path": "apps/myapp/oauth/google",
			"secret_type": "Key-Value Pairs",
			"description": "Google OAuth application credentials",
			"tags": ["production", "oauth"],
			"items": [
				{
					"key": "client_id",
					"value": "123456789012-abcdefghijklmnopqrstuvwxyz123456.apps.googleusercontent.com",
				},
				{"key": "client_secret", "value": "GOCSPX-abcdefghijklmnopqrstuvwx"},
			],
		},
		# Another app
		{
			"title": "Portal Database",
			"path": "apps/portal/database",
			"secret_type": "Key-Value Pairs",
			"description": "Customer portal database credentials",
			"tags": ["production", "database"],
			"items": [
				{"key": "host", "value": "portal-db.example.com"},
				{"key": "port", "value": "3306"},
				{"key": "username", "value": "portal_admin"},
				{"key": "password", "value": "P0rt@l_Secur3_2024!"},
				{"key": "database", "value": "customer_portal"},
			],
		},
		{
			"title": "Portal Redis Cache",
			"path": "apps/portal/redis",
			"secret_type": "Key-Value Pairs",
			"description": "Redis connection for portal caching",
			"tags": ["production", "database"],
			"items": [
				{"key": "host", "value": "redis.production.example.com"},
				{"key": "port", "value": "6379"},
				{"key": "password", "value": "R3d1s_P@ssw0rd_2024"},
			],
		},
		# Infrastructure secrets
		{
			"title": "AWS Production Credentials",
			"path": "infrastructure/aws/production",
			"secret_type": "Key-Value Pairs",
			"description": "AWS access credentials for production environment",
			"tags": ["production"],
			"items": [
				{"key": "access_key_id", "value": "FAKEKEYIOSFODNN7EXAMPLE"},
				{"key": "secret_access_key", "value": "FAKESECRETtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"},
				{"key": "region", "value": "us-east-1"},
			],
		},
		{
			"title": "AWS RDS Master Password",
			"path": "infrastructure/aws/rds_master",
			"secret_type": "Single Value",
			"description": "Master password for AWS RDS instances",
			"tags": ["production", "database"],
			"value": "RDS_M@st3r_P@ssw0rd_V3ry_S3cur3_2024!",
		},
		{
			"title": "AWS S3 Backup Credentials",
			"path": "infrastructure/aws/s3_backup",
			"secret_type": "Key-Value Pairs",
			"description": "S3 credentials for backup operations",
			"tags": ["production"],
			"items": [
				{"key": "bucket", "value": "company-backups-prod"},
				{"key": "access_key_id", "value": "FAKEKEYIOSFODNN7BACKUP"},
				{"key": "secret_access_key", "value": "FAKESECRETtnFEMI/K7MDENG/bPxRfiCYBACKUPKEY"},
			],
		},
		{
			"title": "Kubernetes Service Account",
			"path": "infrastructure/kubernetes/prod-sa",
			"secret_type": "Key-Value Pairs",
			"description": "Production Kubernetes service account credentials",
			"tags": ["production"],
			"items": [
				{
					"key": "token",
					"value": "eyJhbGciOiJSUzI1NiIsImtpZCI6IiJ9.eyJpc3MiOiJrdWJlcm5ldGVzL3NlcnZpY2VhY2NvdW50In0.abcdef123456",
				},
				{
					"key": "ca_cert",
					"value": "-----BEGIN CERTIFICATE-----\nMIIDITCCAgmgAwIBAgIBADANBgkqhkiG9w0BAQsFADAV\n-----END CERTIFICATE-----",
				},
			],
		},
		# Development/Staging secrets
		{
			"title": "Dev Database",
			"path": "apps/myapp/dev/database",
			"secret_type": "Key-Value Pairs",
			"description": "Development database credentials",
			"tags": ["development", "database"],
			"items": [
				{"key": "host", "value": "localhost"},
				{"key": "port", "value": "5432"},
				{"key": "username", "value": "dev_user"},
				{"key": "password", "value": "dev_password_123"},
				{"key": "database", "value": "myapp_dev"},
			],
		},
		{
			"title": "Staging Stripe Test Keys",
			"path": "apps/myapp/staging/stripe",
			"secret_type": "Key-Value Pairs",
			"description": "Stripe test mode keys for staging",
			"tags": ["staging", "payment"],
			"items": [
				{"key": "publishable_key", "value": "pk_FAKE_51234567890abcdefghijklmnop"},
				{"key": "secret_key", "value": "sk_FAKE_51234567890abcdefghijklmnop"},
			],
		},
		# CI/CD secrets
		{
			"title": "GitHub Actions Token",
			"path": "ci-cd/github/actions_token",
			"secret_type": "Single Value",
			"description": "GitHub Actions workflow token",
			"tags": ["ci-cd", "api-key"],
			"value": "ghp_FAKE1234567890abcdefghijklmnopqrstuvwxyz",
		},
		{
			"title": "Docker Hub Credentials",
			"path": "ci-cd/docker/hub",
			"secret_type": "Key-Value Pairs",
			"description": "Docker Hub registry credentials",
			"tags": ["ci-cd"],
			"items": [
				{"key": "username", "value": "company_ci"},
				{"key": "password", "value": "DockerHub_P@ss_2024!"},
			],
		},
		{
			"title": "NPM Registry Token",
			"path": "ci-cd/npm/registry_token",
			"secret_type": "Single Value",
			"description": "NPM registry authentication token",
			"tags": ["ci-cd", "api-key"],
			"value": "npm_FAKE1234567890abcdefghijklmnopqrstuvwxyz",
		},
		# Monitoring/Observability
		{
			"title": "Datadog API Key",
			"path": "monitoring/datadog",
			"secret_type": "Key-Value Pairs",
			"description": "Datadog monitoring credentials",
			"tags": ["production", "monitoring"],
			"items": [
				{"key": "api_key", "value": "1234567890abcdef1234567890abcdef"},
				{"key": "app_key", "value": "abcdef1234567890abcdef1234567890abcdef12"},
			],
		},
		{
			"title": "Sentry DSN",
			"path": "monitoring/sentry/dsn",
			"secret_type": "Single Value",
			"description": "Sentry error tracking DSN",
			"tags": ["production", "monitoring"],
			"value": "https://1234567890abcdef1234567890abcdef@o123456.ingest.sentry.io/1234567",
		},
		{
			"title": "PagerDuty Integration Key",
			"path": "monitoring/pagerduty",
			"secret_type": "Single Value",
			"description": "PagerDuty incident management integration key",
			"tags": ["production", "monitoring"],
			"value": "R01234567890ABCDEFGHIJKLMNOPQRS",
		},
	]

	for secret_data in secrets:
		# Skip if already exists
		if frappe.db.exists("Vault Secret", {"path": secret_data["path"]}):
			continue

		# Create the Vault Secret document
		doc = frappe.new_doc("Vault Secret")
		doc.title = secret_data["title"]
		doc.path = secret_data["path"]
		doc.secret_type = secret_data["secret_type"]
		doc.description = secret_data.get("description", "")
		doc.is_group = 0

		# Add items for Key-Value Pairs
		if secret_data["secret_type"] == "Key-Value Pairs":
			items = secret_data.get("items", [])
			for item in items:
				doc.append("items", {"key": item["key"]})
			# Set values to be written to vault
			doc.flags.secret_value = {item["key"]: item["value"] for item in items}
		elif secret_data["secret_type"] == "Single Value":
			# Set single value to be written to vault
			doc.flags.secret_value = secret_data.get("value", "")

		doc.insert(ignore_permissions=True)

		# Apply tags via Frappe's built-in tagging system
		for tag_name in secret_data.get("tags", []):
			add_tag(tag_name, "Vault Secret", doc.name)

	frappe.db.commit()
