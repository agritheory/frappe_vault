// Copyright (c) 2025, AgriTheory and contributors
// For license information, please see license.txt

frappe.ui.form.ControlPassword = class CustomControlPassword extends frappe.ui.form.ControlPassword {
	make_input() {
		super.make_input()

		// Check if this field is vault-enabled
		if (this.df.vault_enabled) {
			this.setupVaultIntegration()
		}
	}

	setupVaultIntegration() {
		// Add visual indicator that this field is vault-protected
		// The actual storage/retrieval is handled by Python monkey patch
		this.$wrapper.find('.control-input').addClass('vault-protected')
	}
}
