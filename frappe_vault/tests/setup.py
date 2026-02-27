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
	"""Create test secrets with folder hierarchy demonstrating path nesting and tagging."""
	secrets = [
		# Root level
		{
			"title": "Master Encryption Key",
			"path": "master_key",
			"description": "Master encryption key for application",
			"tags": ["production"],
			"value": "mk_8a9f7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b",
		},
		{
			"title": "Admin API Token",
			"path": "admin_token",
			"description": "Administrator API access token",
			"tags": ["production", "api-key"],
			"value": "adm_tok_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
		},
		# Application secrets
		{
			"title": "MyApp Database Credentials",
			"path": "apps/myapp/database",
			"description": "Database connection credentials for MyApp",
			"tags": ["production", "database"],
			"value": '{"host":"db.production.example.com","port":"5432","username":"myapp_user","password":"MyS3cur3P@ssw0rd!2024","database":"myapp_production"}',
		},
		{
			"title": "MyApp SendGrid API Key",
			"path": "apps/myapp/sendgrid",
			"description": "SendGrid API key for email delivery",
			"tags": ["production", "api-key", "smtp"],
			"value": "SG.FAKE_1234567890abcdefghijklmnopqrstuvwxyz.ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
		},
		{
			"title": "MyApp Stripe Credentials",
			"path": "apps/myapp/stripe",
			"description": "Stripe payment processing credentials",
			"tags": ["production", "payment"],
			"value": '{"publishable_key":"pk_FAKE_51234567890abcdefghijklmnop","secret_key":"sk_FAKE_51234567890abcdefghijklmnop","webhook_secret":"whsec_FAKE_1234567890abcdefghijklmnop"}',
		},
		{
			"title": "MyApp OAuth GitHub",
			"path": "apps/myapp/oauth/github",
			"description": "GitHub OAuth application credentials",
			"tags": ["production", "oauth"],
			"value": '{"client_id":"Iv1.a1b2c3d4e5f6g7h8","client_secret":"1234567890abcdef1234567890abcdef12345678"}',
		},
		{
			"title": "MyApp OAuth Google",
			"path": "apps/myapp/oauth/google",
			"description": "Google OAuth application credentials",
			"tags": ["production", "oauth"],
			"value": '{"client_id":"123456789012-abcdefghijklmnopqrstuvwxyz123456.apps.googleusercontent.com","client_secret":"GOCSPX-abcdefghijklmnopqrstuvwx"}',
		},
		# Another app
		{
			"title": "Portal Database",
			"path": "apps/portal/database",
			"description": "Customer portal database credentials",
			"tags": ["production", "database"],
			"value": '{"host":"portal-db.example.com","port":"3306","username":"portal_admin","password":"P0rt@l_Secur3_2024!","database":"customer_portal"}',
		},
		{
			"title": "Portal Redis Cache",
			"path": "apps/portal/redis",
			"description": "Redis connection for portal caching",
			"tags": ["production", "database"],
			"value": '{"host":"redis.production.example.com","port":"6379","password":"R3d1s_P@ssw0rd_2024"}',
		},
		# Infrastructure secrets
		{
			"title": "AWS Production Credentials",
			"path": "infrastructure/aws/production",
			"description": "AWS access credentials for production environment",
			"tags": ["production"],
			"value": '{"access_key_id":"FAKEKEYIOSFODNN7EXAMPLE","secret_access_key":"FAKESECRETtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY","region":"us-east-1"}',
		},
		{
			"title": "AWS RDS Master Password",
			"path": "infrastructure/aws/rds_master",
			"description": "Master password for AWS RDS instances",
			"tags": ["production", "database"],
			"value": "RDS_M@st3r_P@ssw0rd_V3ry_S3cur3_2024!",
		},
		{
			"title": "AWS S3 Backup Credentials",
			"path": "infrastructure/aws/s3_backup",
			"description": "S3 credentials for backup operations",
			"tags": ["production"],
			"value": '{"bucket":"company-backups-prod","access_key_id":"FAKEKEYIOSFODNN7BACKUP","secret_access_key":"FAKESECRETtnFEMI/K7MDENG/bPxRfiCYBACKUPKEY"}',
		},
		{
			"title": "Kubernetes Service Account",
			"path": "infrastructure/kubernetes/prod-sa",
			"description": "Production Kubernetes service account credentials",
			"tags": ["production"],
			"value": '{"token":"eyJhbGciOiJSUzI1NiIsImtpZCI6IiJ9.eyJpc3MiOiJrdWJlcm5ldGVzL3NlcnZpY2VhY2NvdW50In0.abcdef123456","ca_cert":"-----BEGIN CERTIFICATE-----\\nMIIDITCCAgmgAwIBAgIBADANBgkqhkiG9w0BAQsFADAV\\n-----END CERTIFICATE-----"}',
		},
		# Development/Staging
		{
			"title": "Dev Database",
			"path": "apps/myapp/dev/database",
			"description": "Development database credentials",
			"tags": ["development", "database"],
			"value": '{"host":"localhost","port":"5432","username":"dev_user","password":"dev_password_123","database":"myapp_dev"}',
		},
		{
			"title": "Staging Stripe Test Keys",
			"path": "apps/myapp/staging/stripe",
			"description": "Stripe test mode keys for staging",
			"tags": ["staging", "payment"],
			"value": '{"publishable_key":"pk_FAKE_51234567890abcdefghijklmnop","secret_key":"sk_FAKE_51234567890abcdefghijklmnop"}',
		},
		# CI/CD
		{
			"title": "GitHub Actions Token",
			"path": "ci-cd/github/actions_token",
			"description": "GitHub Actions workflow token",
			"tags": ["ci-cd", "api-key"],
			"value": "ghp_FAKE1234567890abcdefghijklmnopqrstuvwxyz",
		},
		{
			"title": "Docker Hub Credentials",
			"path": "ci-cd/docker/hub",
			"description": "Docker Hub registry credentials",
			"tags": ["ci-cd"],
			"value": '{"username":"company_ci","password":"DockerHub_P@ss_2024!"}',
		},
		{
			"title": "NPM Registry Token",
			"path": "ci-cd/npm/registry_token",
			"description": "NPM registry authentication token",
			"tags": ["ci-cd", "api-key"],
			"value": "npm_FAKE1234567890abcdefghijklmnopqrstuvwxyz",
		},
		# Monitoring/Observability
		{
			"title": "Datadog API Key",
			"path": "monitoring/datadog",
			"description": "Datadog monitoring credentials",
			"tags": ["production", "monitoring"],
			"value": '{"api_key":"1234567890abcdef1234567890abcdef","app_key":"abcdef1234567890abcdef1234567890abcdef12"}',
		},
		{
			"title": "Sentry DSN",
			"path": "monitoring/sentry/dsn",
			"description": "Sentry error tracking DSN",
			"tags": ["production", "monitoring"],
			"value": "https://1234567890abcdef1234567890abcdef@o123456.ingest.sentry.io/1234567",
		},
		{
			"title": "PagerDuty Integration Key",
			"path": "monitoring/pagerduty",
			"description": "PagerDuty incident management integration key",
			"tags": ["production", "monitoring"],
			"value": "R01234567890ABCDEFGHIJKLMNOPQRS",
		},
	]

	for secret_data in secrets:
		if frappe.db.exists("Vault Secret", {"path": secret_data["path"]}):
			continue

		doc = frappe.new_doc("Vault Secret")
		doc.title = secret_data["title"]
		doc.path = secret_data["path"]
		doc.description = secret_data.get("description", "")
		doc.flags.secret_value = secret_data["value"]
		doc.insert(ignore_permissions=True)

		for tag_name in secret_data.get("tags", []):
			add_tag(tag_name, "Vault Secret", doc.name)

	frappe.db.commit()
