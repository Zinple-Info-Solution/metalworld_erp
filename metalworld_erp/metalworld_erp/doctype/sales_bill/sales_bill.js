// Copyright (c) 2026, akshay and contributors
// For license information, please see license.txt

const SALES_BILL = "metalworld_erp.metalworld_erp.doctype.sales_bill.sales_bill";

frappe.ui.form.on("Sales Bill", {
	setup(frm) {
		frm.set_query("warehouse", () => ({ filters: { company: frm.doc.company, is_group: 0 } }));
		frm.set_query("taxes_and_charges", () => ({ filters: { company: frm.doc.company } }));
		frm.set_query("pos_profile", () => ({ filters: { company: frm.doc.company, disabled: 0 } }));
		frm.set_query("mode_of_payment", () => ({
			filters: { enabled: 1, type: frm.doc.is_bank ? "Bank" : "Cash" },
		}));
		frm.set_query("item_code", "items", () => ({
			filters: { is_sales_item: 1, disabled: 0, has_variants: 0 },
		}));
	},

	onload(frm) {
		if (frm.is_new() && !frm.doc.company) {
			frm.set_value("company", frappe.defaults.get_user_default("Company"));
		}
	},

	refresh(frm) {
		frm._tax_rates = frm._tax_rates || {};
		if (frm.doc.docstatus === 0) {
			const imports = [
				["Quotation", "make_from_quotation", "party_name", { quotation_to: "Customer", status: ["not in", ["Ordered", "Lost", "Expired", "Cancelled"]] }],
				["Sales Order", "make_from_sales_order", "customer", { per_delivered: ["<", 99.99], status: ["not in", ["Closed", "On Hold"]] }],
			];
			for (const [doctype, method, customer_field, filters] of imports) {
				frm.add_custom_button(__(doctype), () => import_from(frm, doctype, method, customer_field, filters), __("Import"));
			}
		}
		frm.add_custom_button(__("Customer Last Prices"), () => show_last_prices(frm));
		load_tax_rates(frm);
	},

	company(frm) {
		set_bill_defaults(frm, false);
	},

	pos_profile(frm) {
		if (frm.doc.pos_profile) set_bill_defaults(frm, true);
	},

	customer(frm) {
		update_previous_balance(frm);
	},

	posting_date(frm) {
		update_previous_balance(frm);
	},

	sales_type(frm) {
		if (frm.doc.sales_type !== "Cash") {
			frm.set_value({ is_bank: 0, mode_of_payment: "" });
		} else {
			set_mode_of_payment(frm);
		}
		calculate(frm);
	},

	is_bank(frm) {
		set_mode_of_payment(frm);
	},

	taxes_and_charges(frm) {
		frm._tax_rates = {};
		load_tax_rates(frm);
	},
});

frappe.ui.form.on("Sales Bill Item", {
	item_code(frm, cdt, cdn) {
		fetch_item_details(frm, cdt, cdn);
	},
	uom(frm, cdt, cdn) {
		fetch_item_details(frm, cdt, cdn, locals[cdt][cdn].uom);
	},
	item_tax_template(frm) {
		load_tax_rates(frm);
	},
	qty: calculate_form,
	rate: calculate_form,
	is_foc: calculate_form,
	items_remove: calculate_form,
});

// overwrite: a newly picked POS Profile replaces the warehouse, price list and VAT Template.
function set_bill_defaults(frm, overwrite) {
	if (!frm.doc.company) return;
	frappe.call({
		method: `${SALES_BILL}.get_bill_defaults`,
		args: { company: frm.doc.company, pos_profile: frm.doc.pos_profile },
		callback: ({ message }) => {
			for (const [field, value] of Object.entries(message || {})) {
				if (field === "pos_profile" && frm.doc.pos_profile) continue;
				if (value && (overwrite || !frm.doc[field]) && frm.doc[field] !== value) frm.set_value(field, value);
			}
		},
	});
}

function calculate_form(frm) {
	calculate(frm);
}

function fetch_item_details(frm, cdt, cdn, uom) {
	const row = locals[cdt][cdn];
	if (!row.item_code) return;
	frappe.call({
		method: `${SALES_BILL}.get_item_details`,
		args: {
			item_code: row.item_code,
			company: frm.doc.company,
			warehouse: frm.doc.warehouse,
			price_list: frm.doc.selling_price_list,
			customer: frm.doc.customer,
			uom,
		},
		callback: ({ message }) => {
			if (!message) return;
			if (uom) delete message.item_tax_template; // keep the user's VAT Group on unit change
			frappe.model.set_value(cdt, cdn, message).then(() => load_tax_rates(frm));
		},
	});
}

// VAT % per item tax template, computed server-side the same way Sales Invoice applies it.
function load_tax_rates(frm) {
	const templates = [...new Set((frm.doc.items || []).map((d) => d.item_tax_template).filter(Boolean))];
	const missing = !("" in (frm._tax_rates || {})) || templates.some((t) => !(t in frm._tax_rates));
	if (!missing) {
		calculate(frm);
		return;
	}
	frappe.call({
		method: `${SALES_BILL}.get_tax_rates`,
		args: { taxes_and_charges: frm.doc.taxes_and_charges, item_tax_templates: templates },
		callback: ({ message }) => {
			frm._tax_rates = message || { "": 0 };
			calculate(frm);
		},
	});
}

function calculate(frm) {
	if (frm.doc.docstatus !== 0) return;
	const rates = frm._tax_rates || {};
	const items = frm.doc.items || [];
	let total_amount = 0;
	let total_tax = 0;
	let total_qty = 0;

	for (const d of items) {
		if (d.is_foc) d.rate = 0;
		d.tax_rate = flt(rates[d.item_tax_template || ""]);
		d.amount = flt(flt(d.qty) * flt(d.rate), precision("amount", d));
		d.tax_amount = flt((d.amount * d.tax_rate) / 100, precision("tax_amount", d));
		d.rate_inc_vat = flt(flt(d.rate) * (1 + d.tax_rate / 100), precision("rate_inc_vat", d));
		d.amount_inc_vat = flt(d.amount + d.tax_amount, precision("amount_inc_vat", d));
		total_qty += flt(d.qty);
		total_amount += d.amount;
		total_tax += d.tax_amount;
	}

	const grand_total = flt(total_amount + total_tax, precision("grand_total"));
	const received_amount = frm.doc.sales_type === "Cash" ? grand_total : 0;
	Object.assign(frm.doc, {
		default_tax_rate: flt(rates[""]),
		total_items: items.length,
		total_qty,
		total_amount: flt(total_amount, precision("total_amount")),
		total_tax: flt(total_tax, precision("total_tax")),
		grand_total,
		received_amount,
		net_balance: flt(frm.doc.previous_balance) + grand_total - received_amount,
	});
	frm.refresh_fields();
}

function update_previous_balance(frm) {
	if (!frm.doc.customer || !frm.doc.company || frm.doc.docstatus !== 0) return;
	frappe.call({
		method: `${SALES_BILL}.get_customer_balance`,
		args: { customer: frm.doc.customer, company: frm.doc.company, date: frm.doc.posting_date },
		callback: ({ message }) => {
			frm.doc.previous_balance = flt(message);
			calculate(frm);
		},
	});
}

function set_mode_of_payment(frm) {
	if (frm.doc.sales_type !== "Cash") return;
	frappe.db
		.get_list("Mode of Payment", {
			filters: { enabled: 1, type: frm.doc.is_bank ? "Bank" : "Cash" },
			limit: 1,
		})
		.then((rows) => frm.set_value("mode_of_payment", rows.length ? rows[0].name : ""));
}

function import_from(frm, source_doctype, method, customer_field, filters) {
	erpnext.utils.map_current_doc({
		method: `${SALES_BILL}.${method}`,
		source_doctype,
		target: frm,
		date_field: "transaction_date",
		setters: { [customer_field]: frm.doc.customer || undefined },
		get_query_filters: { docstatus: 1, company: frm.doc.company, ...filters },
	});
}

function show_last_prices(frm) {
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
	const esc = frappe.utils.escape_html;
	const load = () =>
		frappe.call({
			method: `${SALES_BILL}.get_last_selling_rates`,
			args: { customer: frm.doc.customer, item_code: dialog.get_value("item_code") },
			callback: ({ message }) => {
				const rows = (message || [])
					.map(
						(d) => `<tr>
							<td>${frappe.datetime.str_to_user(d.posting_date)}</td>
							<td><a href="/app/${frappe.router.slug(d.doctype)}/${encodeURIComponent(d.invoice)}">${esc(d.invoice)}</a></td>
							<td>${esc(d.item_code)}: ${esc(d.item_name || "")}</td>
							<td class="text-right">${format_number(d.qty)} ${esc(d.uom || "")}</td>
							<td class="text-right">${format_currency(d.rate)}</td>
						</tr>`
					)
					.join("");
				dialog.fields_dict.result.$wrapper.html(`
					<table class="table table-bordered table-condensed">
						<thead><tr><th>${__("Date")}</th><th>${__("Invoice")}</th><th>${__("Item")}</th>
							<th class="text-right">${__("Qty")}</th><th class="text-right">${__("Rate")}</th></tr></thead>
						<tbody>${rows || `<tr><td colspan="5" class="text-muted text-center">${__("No previous sales found.")}</td></tr>`}</tbody>
					</table>`);
			},
		});
	dialog.show();
	load();
}
