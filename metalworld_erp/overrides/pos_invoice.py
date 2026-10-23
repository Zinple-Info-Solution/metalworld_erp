import json

import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import update_multi_mode_option
from erpnext.accounts.utils import get_balance_on
from frappe import _
from frappe.model.mapper import get_mapped_doc
from frappe.utils import cint, flt


def before_validate(doc, method=None):
	apply_foc(doc)

	if doc.is_return or not doc.items:
		return

	if doc.sales_type == "Credit":
		validate_credit_allowed(doc)
		for payment in doc.payments:
			payment.amount = payment.base_amount = 0
		return

	# Cash: totals must be current before the payment is set, because ERPNext's
	# validate_full_payment compares paid_amount against the recalculated total.
	doc.calculate_taxes_and_totals()
	set_full_payment(doc)


def validate(doc, method=None):
	set_item_tax_details(doc)

	if doc.docstatus == 0:
		set_item_cost(doc)
		set_previous_balance(doc)

	if doc.change_cash_customer_name and doc.cash_customer_name:
		doc.title = doc.cash_customer_name


def apply_foc(doc):
	for item in doc.items:
		if item.is_foc:
			item.discount_percentage = 100
			item.rate = 0


def validate_credit_allowed(doc):
	if not frappe.db.get_value("POS Profile", doc.pos_profile, "allow_partial_payment"):
		frappe.throw(
			_("POS Profile {0} must have {1} enabled to make Credit invoices.").format(
				frappe.bold(doc.pos_profile), frappe.bold(_("Allow Partial Payment"))
			),
			title=_("Credit Not Allowed"),
		)


def set_full_payment(doc):
	if not doc.payments and doc.pos_profile:
		update_multi_mode_option(doc, frappe.get_cached_doc("POS Profile", doc.pos_profile))

	if not doc.payments:
		frappe.throw(_("POS Profile {0} has no Mode of Payment configured.").format(doc.pos_profile))

	wanted_type = "Bank" if doc.is_bank else "Cash"
	target = next((p for p in doc.payments if p.type == wanted_type), None)
	if not target:
		if doc.is_bank:
			frappe.throw(
				_("POS Profile {0} has no Mode of Payment of type Bank.").format(frappe.bold(doc.pos_profile))
			)
		target = next((p for p in doc.payments if p.default), doc.payments[0])

	invoice_total = flt(doc.rounded_total) or flt(doc.grand_total)
	for payment in doc.payments:
		payment.amount = invoice_total if payment is target else 0
		payment.base_amount = flt(payment.amount * flt(doc.conversion_rate), payment.precision("base_amount"))


def get_item_tax_rate(item, taxes):
	"""Combined VAT % for a row: tax rates on Net Total, overridden by the row's Item Tax Template."""
	item_tax_rate = json.loads(item.item_tax_rate) if item.item_tax_rate else {}
	return sum(
		flt(item_tax_rate.get(tax.account_head, tax.rate))
		for tax in taxes
		if tax.charge_type == "On Net Total"
	)


def set_item_tax_details(doc):
	for item in doc.items:
		item.vat_rate = get_item_tax_rate(item, doc.taxes)
		item.vat_amount = flt(item.net_amount * item.vat_rate / 100, item.precision("vat_amount"))
		item.rate_inc_vat = flt(item.net_rate * (1 + item.vat_rate / 100), item.precision("rate_inc_vat"))
		item.amount_inc_vat = flt(item.net_amount + item.vat_amount, item.precision("amount_inc_vat"))


def set_item_cost(doc):
	for item in doc.items:
		if not item.item_code:
			continue
		item.item_cost = (
			frappe.db.get_value(
				"Bin", {"item_code": item.item_code, "warehouse": item.warehouse}, "valuation_rate"
			)
			or frappe.db.get_value("Item", item.item_code, "valuation_rate")
			or 0
		) * flt(item.conversion_factor or 1)


def set_previous_balance(doc):
	if not doc.customer:
		return

	ledger_balance = get_balance_on(
		party_type="Customer",
		party=doc.customer,
		company=doc.company,
		date=doc.posting_date,
		ignore_account_permission=True,
	)
	# Submitted POS Invoices only reach the ledger once they are consolidated at POS closing.
	unconsolidated = frappe.db.sql(
		"""
		select coalesce(sum(outstanding_amount), 0)
		from `tabPOS Invoice`
		where customer = %s and company = %s and docstatus = 1
			and ifnull(consolidated_invoice, '') = '' and name != %s
		""",
		(doc.customer, doc.company, doc.name or ""),
	)[0][0]

	doc.previous_balance = flt(ledger_balance) + flt(unconsolidated)
	doc.total_due = doc.previous_balance + flt(doc.outstanding_amount)


def _postprocess(source, target):
	target.is_pos = 1
	target.run_method("set_missing_values")
	target.run_method("calculate_taxes_and_totals")


@frappe.whitelist()
def make_from_sales_order(source_name, target_doc=None):
	def update_item(source, target, source_parent):
		target.qty = flt(source.qty) - flt(source.delivered_qty)

	return get_mapped_doc(
		"Sales Order",
		source_name,
		{
			"Sales Order": {
				"doctype": "POS Invoice",
				"field_no_map": ["payment_terms_template", "naming_series"],
				"validation": {"docstatus": ["=", 1]},
			},
			"Sales Order Item": {
				"doctype": "POS Invoice Item",
				"field_map": {"name": "so_detail", "parent": "sales_order"},
				"postprocess": update_item,
				"condition": lambda d: flt(d.qty) > flt(d.delivered_qty) and not d.delivered_by_supplier,
			},
			"Sales Taxes and Charges": {"doctype": "Sales Taxes and Charges", "reset_value": True},
		},
		target_doc,
		_postprocess,
	)


@frappe.whitelist()
def make_from_quotation(source_name, target_doc=None):
	def set_customer(source, target, source_parent=None):
		if source.quotation_to != "Customer":
			frappe.throw(_("Quotation {0} is not for a Customer.").format(source.name))
		target.customer = source.party_name

	return get_mapped_doc(
		"Quotation",
		source_name,
		{
			"Quotation": {
				"doctype": "POS Invoice",
				"field_no_map": ["payment_terms_template", "naming_series"],
				"validation": {"docstatus": ["=", 1]},
				"postprocess": set_customer,
			},
			"Quotation Item": {"doctype": "POS Invoice Item"},
			"Sales Taxes and Charges": {"doctype": "Sales Taxes and Charges", "reset_value": True},
		},
		target_doc,
		_postprocess,
	)


@frappe.whitelist()
def get_last_selling_rates(customer, item_code=None, limit=10):
	"""Recent selling rates to a customer, from POS Invoices and (non-consolidated) Sales Invoices."""
	frappe.has_permission("POS Invoice", "read", throw=True)
	limit = cint(limit) or 10
	item_filter = "and item.item_code = %(item_code)s" if item_code else ""

	queries = [
		f"""
		select inv.posting_date, 'POS Invoice' as doctype, inv.name as invoice, item.item_code,
			item.item_name, item.qty, item.uom, item.rate, item.discount_percentage
		from `tabPOS Invoice` inv join `tabPOS Invoice Item` item on item.parent = inv.name
		where inv.customer = %(customer)s and inv.docstatus = 1 and inv.is_return = 0 {item_filter}
		"""
	]
	# POS Invoices are merged into consolidated Sales Invoices; skip those to avoid duplicates.
	if frappe.has_permission("Sales Invoice", "read"):
		queries.append(
			f"""
			select inv.posting_date, 'Sales Invoice' as doctype, inv.name as invoice, item.item_code,
				item.item_name, item.qty, item.uom, item.rate, item.discount_percentage
			from `tabSales Invoice` inv join `tabSales Invoice Item` item on item.parent = inv.name
			where inv.customer = %(customer)s and inv.docstatus = 1 and inv.is_return = 0
				and inv.is_consolidated = 0 {item_filter}
			"""
		)

	return frappe.db.sql(
		" union all ".join(queries) + " order by posting_date desc, invoice desc limit %(limit)s",
		{"customer": customer, "item_code": item_code, "limit": limit},
		as_dict=True,
	)
