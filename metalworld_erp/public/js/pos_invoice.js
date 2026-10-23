const MW_POS_METHOD = "metalworld_erp.overrides.pos_invoice";

frappe.ui.form.on("POS Invoice", {
	refresh(frm) {
		if (frm.doc.docstatus !== 0) return;

		frm.add_custom_button(
			__("Quotation"),
			() => mw_import(frm, "Quotation", "make_from_quotation", {
				quotation_to: "Customer",
				party_name: frm.doc.customer || undefined,
				status: ["not in", ["Ordered", "Lost", "Expired", "Cancelled"]],
			}),
			__("Import")
		);
		frm.add_custom_button(
			__("Sales Order"),
			() => mw_import(frm, "Sales Order", "make_from_sales_order", {
				customer: frm.doc.customer || undefined,
				per_delivered: ["<", 99.99],
				status: ["not in", ["Closed", "On Hold"]],
			}),
			__("Import")
		);
		frm.add_custom_button(__("Last Selling Prices"), () => mw_last_selling_prices(frm));
	},

	sales_type(frm) {
		if (frm.doc.sales_type !== "Cash") frm.set_value("is_bank", 0);
	},
});

frappe.ui.form.on("POS Invoice Item", {
	is_foc(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.model.set_value(cdt, cdn, "discount_percentage", row.is_foc ? 100 : 0);
		if (row.is_foc) frappe.model.set_value(cdt, cdn, "rate", 0);
	},
});

function mw_import(frm, source_doctype, method, filters) {
	erpnext.utils.map_current_doc({
		method: `${MW_POS_METHOD}.${method}`,
		source_doctype,
		target: frm,
		date_field: "transaction_date",
		setters:
			source_doctype === "Quotation"
				? { party_name: frm.doc.customer || undefined }
				: { customer: frm.doc.customer || undefined },
		get_query_filters: { docstatus: 1, company: frm.doc.company, ...filters },
	});
}

function mw_last_selling_prices(frm) {
	if (!frm.doc.customer) {
		frappe.msgprint(__("Select a Customer first."));
		return;
	}

	const last_item = (frm.doc.items || []).filter((d) => d.item_code).pop();
	const dialog = new frappe.ui.Dialog({
		title: __("Last Selling Prices: {0}", [frm.doc.customer_name || frm.doc.customer]),
		size: "large",
		fields: [
			{
				fieldname: "item_code",
				fieldtype: "Link",
				options: "Item",
				label: __("Item (leave empty for all)"),
				default: last_item && last_item.item_code,
				onchange: () => load(),
			},
			{ fieldname: "result", fieldtype: "HTML" },
		],
	});

	const load = () => {
		frappe.call({
			method: `${MW_POS_METHOD}.get_last_selling_rates`,
			args: { customer: frm.doc.customer, item_code: dialog.get_value("item_code"), limit: 20 },
			callback: (r) => {
				const rows = r.message || [];
				const body = rows.length
					? rows
						.map(
							(d) => `<tr>
								<td>${frappe.datetime.str_to_user(d.posting_date)}</td>
								<td><a href="/app/${frappe.router.slug(d.doctype)}/${encodeURIComponent(d.invoice)}">${frappe.utils.escape_html(d.invoice)}</a></td>
								<td>${frappe.utils.escape_html(d.item_code)}: ${frappe.utils.escape_html(d.item_name || "")}</td>
								<td class="text-right">${format_number(d.qty)} ${frappe.utils.escape_html(d.uom || "")}</td>
								<td class="text-right">${format_currency(d.rate, frm.doc.currency)}</td>
								<td class="text-right">${flt(d.discount_percentage) ? format_number(d.discount_percentage) + "%" : ""}</td>
							</tr>`
						)
						.join("")
					: `<tr><td colspan="6" class="text-muted text-center">${__("No previous sales found.")}</td></tr>`;

				dialog.fields_dict.result.$wrapper.html(`
					<table class="table table-bordered table-condensed">
						<thead><tr>
							<th>${__("Date")}</th><th>${__("Invoice")}</th><th>${__("Item")}</th>
							<th class="text-right">${__("Qty")}</th><th class="text-right">${__("Rate")}</th>
							<th class="text-right">${__("Disc.")}</th>
						</tr></thead>
						<tbody>${body}</tbody>
					</table>`);
			},
		});
	};

	dialog.show();
	load();
}
