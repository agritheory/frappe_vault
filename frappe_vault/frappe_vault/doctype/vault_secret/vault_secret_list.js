// Copyright (c) 2025, AgriTheory and contributors
// For license information, please see license.txt

frappe.listview_settings['Vault Secret'] = {
	onload(listview) {
		frappe.call({
			method: 'frappe_vault.vault_proxy.status',
			callback({ message }) {
				if (!message?.secrets_api_enabled) {
					// Remove the "Add Vault Secret" header button
					listview.can_create = false
					listview.set_primary_action()

					// Replace the empty-state function and unconditionally update the DOM.
					// toggle_result_area() only calls .toggle() — it never re-renders content —
					// so we must set the HTML now regardless of current visibility.  If the
					// no-result div is already shown the user sees it immediately; if it is
					// still hidden the custom content is in place before toggle() shows it.
					listview.get_no_result_message = () => `
					<div class="msg-box no-border">
						<div>
							<img src="/assets/frappe/images/ui-states/list-empty-state.svg"
								alt="Vault Secrets disabled" class="null-state">
						</div>
						<p>${__('Secrets UI must be enabled in the site config.')}</p>
					</div>`

					listview.$no_result?.html(listview.get_no_result_message())
				}
			},
		})
	},
}
