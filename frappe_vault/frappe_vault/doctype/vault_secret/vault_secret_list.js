// Copyright (c) 2025, AgriTheory and contributors
// For license information, please see license.txt

frappe.listview_settings['Vault Secret'] = {
	onload(listview) {
		// Kick off the status check early using xcall (returns a native Promise so
		// the refresh hook can await it with .then()).  frappe.call returns a jQuery
		// Deferred which is not reliably thenable in this context.
		listview._vault_status = frappe.xcall('frappe_vault.vault_proxy.status')
	},

	refresh(listview) {
		// refresh fires AFTER toggle_result_area() — .no-result is already shown or
		// hidden based on the record count at this point.  We resolve the status
		// promise here so that .html() always targets a visible element, eliminating
		// the race between the XHR response and the DOM visibility state.
		if (!listview._vault_status) return
		listview._vault_status.then(status => {
			if (status?.secrets_api_enabled) return

			listview.can_create = false
			listview.set_primary_action()

			listview.get_no_result_message = () => `
				<div class="msg-box no-border">
					<div>
						<img src="/assets/frappe/images/ui-states/list-empty-state.svg"
							alt="Vault Secrets disabled" class="null-state">
					</div>
					<p>${__('Secrets UI must be enabled in the site config.')}</p>
				</div>`

			listview.$no_result?.html(listview.get_no_result_message())
		})
	},
}
