# Copyright (c) 2026, akshay and contributors
# For license information, please see license.txt

import json

import frappe
from erpnext.accounts.utils import get_balance_on
from erpnext.controllers.accounts_controller import get_default_taxes_and_charges
from erpnext.stock.get_item_details import get_conversion_factor, get_pos_profile
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.query_builder import Case
from frappe.query_builder.functions import IfNull, Sum
from frappe.utils import flt, getdate, nowdate


class SalesBill(Document):
	def validate(self):
		self.set_defaults()
		self.validate_items()
		self.calculate_totals()
		if self.docstatus == 0:
			self.previous_balance = get_customer_balance(self.customer, self.company, self.posting_date)
		self.net_balance = flt(self.previous_balance) + flt(self.grand_total) - flt(self.received_amount)

	def on_submit(self):
		self.db_set("pos_invoice", self.make_pos_invoice().name)

	def on_cancel(self):
		if self.pos_invoice and frappe.db.get_value("POS Invoice", self.pos_invoice, "docstatus") == 1:
			frappe.get_doc("POS Invoice", self.pos_invoice).cancel()

	def set_defaults(self):
		if self.sales_type != "Cash":
			self.is_bank = 0
			self.mode_of_payment = None
		if self.account_no and not self.customer:
			self.customer = get_customer_by_account_no(self.account_no)
		if self.customer:
			self.customer_name, self.account_no = frappe.db.get_value(
				"Customer", self.customer, ["customer_name", "account_no"]
			)
		if not self.pos_profile:
			self.pos_profile = (get_pos_profile(self.company) or {}).get("name")
		if not self.taxes_and_charges:
			self.taxes_and_charges = get_default_taxes_and_charges(
				"Sales Taxes and Charges Template", company=self.company
			).get("taxes_and_charges")

	def validate_items(self):
		for d in self.items:
			if not d.uom:
				d.uom = d.stock_uom or frappe.get_cached_value("Item", d.item_code, "stock_uom")
			if not flt(d.conversion_factor):
				d.conversion_factor = get_conversion_factor(d.item_code, d.uom)["conversion_factor"]

	def calculate_totals(self):
		rates = get_tax_rates(self.taxes_and_charges, [d.item_tax_template for d in self.items])
		self.default_tax_rate = rates[""]

		for d in self.items:
			if d.is_foc:
				d.rate = 0
			d.amount = flt(flt(d.qty) * flt(d.rate), d.precision("amount"))
			d.tax_rate = rates[d.item_tax_template or ""]
			d.tax_amount = flt(d.amount * d.tax_rate / 100, d.precision("tax_amount"))
			d.rate_inc_vat = flt(flt(d.rate) * (1 + d.tax_rate / 100), d.precision("rate_inc_vat"))
			d.amount_inc_vat = flt(d.amount + d.tax_amount, d.precision("amount_inc_vat"))

		self.total_items = len(self.items)
		self.total_qty = sum(flt(d.qty) for d in self.items)
		self.total_amount = flt(sum(d.amount for d in self.items), self.precision("total_amount"))
		self.total_tax = flt(sum(d.tax_amount for d in self.items), self.precision("total_tax"))
		self.grand_total = flt(self.total_amount + self.total_tax, self.precision("grand_total"))
		self.received_amount = self.grand_total if self.sales_type == "Cash" else 0

	def make_pos_invoice(self):
		inv = frappe.new_doc("POS Invoice")
		inv.update(
			{
				"is_pos": 1,
				"pos_profile": self.pos_profile,
				"company": self.company,
				"customer": self.customer,
				"posting_date": self.posting_date,
				"set_posting_time": 1,
				"po_no": self.po_no,
				"po_date": self.po_date,
				"remarks": _("Against Sales Bill {0}").format(self.name)
				+ (f"\n{self.remarks}" if self.remarks else ""),
			}
		)
		for d in self.items:
			inv.append(
				"items",
				{
					"item_code": d.item_code,
					"qty": d.qty,
					"uom": d.uom,
					"conversion_factor": d.conversion_factor,
					"rate": d.rate,
					# 100% discount stops ERPNext from re-filling a zero rate from the price list
					"discount_percentage": 100 if d.is_foc else 0,
					"item_tax_template": d.item_tax_template,
					"warehouse": self.warehouse,
					"sales_order": d.sales_order,
					"so_detail": d.so_detail,
				},
			)

		# Pulls payment modes and defaults from the POS Profile, then re-apply the bill's own settings.
		inv.set_missing_values()
		inv.update(
			{
				"selling_price_list": self.selling_price_list or inv.selling_price_list,
				"set_warehouse": self.warehouse,
				"ignore_pricing_rule": 1,
				"disable_rounded_total": 1,
			}
		)
		for d in inv.items:
			d.warehouse = self.warehouse
		if inv.taxes_and_charges != self.taxes_and_charges:
			inv.taxes_and_charges = self.taxes_and_charges
			inv.set("taxes", [])
		if inv.taxes_and_charges and not inv.taxes:
			inv.set_taxes()

		inv.calculate_taxes_and_totals()
		if abs(flt(inv.grand_total) - flt(self.grand_total)) > 0.05:
			frappe.throw(
				_(
					"POS Invoice total {0} does not match Sales Bill total {1}. "
					"Check the VAT Template and the items' VAT Group."
				).format(inv.grand_total, self.grand_total),
				title=_("Total Mismatch"),
			)

		# POS Invoice rounds per Global Defaults (it has no Disable Rounded Total of its own).
		invoice_total = flt(inv.rounded_total) or flt(inv.grand_total)
		self.set_pos_payment(inv, invoice_total)
		# POS Invoice never computes this itself (the consolidated Sales Invoice does);
		# set it so Credit bills show as Unpaid until the POS Closing Entry.
		inv.outstanding_amount = flt(
			invoice_total - sum(flt(p.amount) for p in inv.payments), inv.precision("outstanding_amount")
		)
		inv.insert()
		inv.submit()
		return inv

	def set_pos_payment(self, inv, invoice_total):
		"""Cash pays the full amount through the bill's Mode of Payment; Credit leaves it outstanding."""
		for p in inv.payments:
			p.amount = 0
		if self.sales_type != "Cash":
			if not frappe.db.get_value("POS Profile", self.pos_profile, "allow_partial_payment"):
				frappe.throw(
					_("Enable Allow Partial Payment in POS Profile {0} to make Credit bills.").format(
						frappe.bold(self.pos_profile)
					),
					title=_("Credit Not Allowed"),
				)
			return

		payment = next((p for p in inv.payments if p.mode_of_payment == self.mode_of_payment), None)
		if not payment:
			frappe.throw(
				_("Mode of Payment {0} is not set in POS Profile {1}.").format(
					frappe.bold(self.mode_of_payment), frappe.bold(self.pos_profile)
				)
			)
		payment.amount = invoice_total


@frappe.whitelist()
def get_customer_balance(customer, company, date=None):
	frappe.has_permission("Sales Bill", "read", throw=True)
	if not (customer and company):
		return 0
	date = date or nowdate()
	ledger_balance = get_balance_on(
		party_type="Customer",
		party=customer,
		company=company,
		date=date,
		ignore_account_permission=True,
	)
	# POS Invoices post to the ledger only when the POS Closing Entry consolidates them.
	pos = frappe.qb.DocType("POS Invoice")
	unconsolidated = (
		frappe.qb.from_(pos)
		.select(
			Sum(
				Case().when(pos.rounded_total != 0, pos.rounded_total).else_(pos.grand_total)
				- pos.paid_amount
				+ pos.change_amount
				- pos.write_off_amount
			)
		)
		.where(
			(pos.customer == customer)
			& (pos.company == company)
			& (pos.docstatus == 1)
			& (pos.posting_date <= date)
			& (IfNull(pos.consolidated_invoice, "") == "")
		)
	).run()
	return flt(ledger_balance) + flt(unconsolidated[0][0])


@frappe.whitelist()
def get_customer_by_account_no(account_no):
	frappe.has_permission("Customer", "read", throw=True)
	customer = frappe.db.get_value("Customer", {"account_no": (account_no or "").strip()})
	if not customer:
		frappe.throw(_("No Customer found with A/C No {0}.").format(frappe.bold(account_no)))
	return customer


@frappe.whitelist()
def get_tax_rates(taxes_and_charges=None, item_tax_templates=None):
	"""VAT % per Item Tax Template ("" = no template), computed the way Sales Invoice applies them:
	each On Net Total row of the VAT Template, overridden by the item template's rate for that account."""
	if isinstance(item_tax_templates, str):
		item_tax_templates = json.loads(item_tax_templates)

	tax_rows = []
	if taxes_and_charges:
		tax_rows = frappe.get_all(
			"Sales Taxes and Charges",
			filters={
				"parent": taxes_and_charges,
				"parenttype": "Sales Taxes and Charges Template",
				"charge_type": "On Net Total",
			},
			fields=["account_head", "rate"],
		)

	rates = {"": sum(flt(t.rate) for t in tax_rows)}
	for template in set(filter(None, item_tax_templates or [])):
		overrides = dict(
			frappe.get_all(
				"Item Tax Template Detail",
				filters={"parent": template},
				fields=["tax_type", "tax_rate"],
				as_list=True,
			)
		)
		rates[template] = sum(flt(overrides.get(t.account_head, t.rate)) for t in tax_rows)
	return rates


@frappe.whitelist()
def get_bill_defaults(company, pos_profile=None):
	"""Warehouse, price list and VAT Template from the POS Profile, falling back to the global defaults."""
	if not pos_profile:
		pos_profile = (get_pos_profile(company) or {}).get("name")
	profile = (
		frappe.db.get_value(
			"POS Profile", pos_profile, ["warehouse", "selling_price_list", "taxes_and_charges"], as_dict=True
		)
		if pos_profile
		else None
	) or frappe._dict()
	return {
		"pos_profile": pos_profile,
		"warehouse": profile.warehouse or frappe.db.get_single_value("Stock Settings", "default_warehouse"),
		"selling_price_list": profile.selling_price_list
		or frappe.db.get_single_value("Selling Settings", "selling_price_list"),
		"taxes_and_charges": profile.taxes_and_charges
		or get_default_taxes_and_charges("Sales Taxes and Charges Template", company=company).get(
			"taxes_and_charges"
		),
	}


@frappe.whitelist()
def get_item_details(item_code, company, warehouse=None, price_list=None, customer=None, uom=None):
	item = frappe.get_cached_doc("Item", item_code)
	uom = uom or item.sales_uom or item.stock_uom
	conversion_factor = flt(get_conversion_factor(item_code, uom)["conversion_factor"]) or 1

	valuation_rate = (
		warehouse
		and frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "valuation_rate")
	) or item.valuation_rate

	return {
		"item_name": item.item_name,
		"stock_uom": item.stock_uom,
		"uom": uom,
		"conversion_factor": conversion_factor,
		"rate": get_item_price(item_code, price_list, customer, uom, item.stock_uom, conversion_factor),
		"cost": flt(valuation_rate) * conversion_factor,
		"item_tax_template": next(
			(t.item_tax_template for t in item.taxes if not t.valid_from or getdate(t.valid_from) <= getdate()),
			None,
		),
	}


def get_item_price(item_code, price_list, customer, uom, stock_uom, conversion_factor):
	if not price_list:
		return 0
	prices = frappe.get_all(
		"Item Price",
		filters={
			"item_code": item_code,
			"price_list": price_list,
			"uom": ["in", [uom, stock_uom]],
		},
		or_filters=[["customer", "is", "not set"]] + ([["customer", "=", customer]] if customer else []),
		fields=["price_list_rate", "uom", "customer"],
		order_by="customer desc, valid_from desc",
	)
	# Customer-specific prices sort first; within those, prefer the selected unit,
	# otherwise scale a stock-unit price by the conversion factor.
	for p in prices:
		if p.uom == uom:
			return flt(p.price_list_rate)
	return flt(prices[0].price_list_rate) * conversion_factor if prices else 0


@frappe.whitelist()
def get_last_selling_rates(customer, item_code=None, limit=20):
	frappe.has_permission("Sales Bill", "read", throw=True)
	rows = []
	for doctype in ("POS Invoice", "Sales Invoice"):
		inv = frappe.qb.DocType(doctype)
		item = frappe.qb.DocType(f"{doctype} Item")
		query = (
			frappe.qb.from_(inv)
			.join(item)
			.on(item.parent == inv.name)
			.select(
				inv.posting_date,
				inv.name.as_("invoice"),
				item.item_code,
				item.item_name,
				item.qty,
				item.uom,
				item.rate,
			)
			.where((inv.customer == customer) & (inv.docstatus == 1) & (inv.is_return == 0))
			.orderby(inv.posting_date, order=frappe.qb.desc)
			.orderby(inv.creation, order=frappe.qb.desc)
			.limit(int(limit))
		)
		if doctype == "Sales Invoice":
			query = query.where(inv.is_consolidated == 0)
		if item_code:
			query = query.where(item.item_code == item_code)
		for row in query.run(as_dict=True):
			row.doctype = doctype
			rows.append(row)
	rows.sort(key=lambda d: d.posting_date, reverse=True)
	return rows[: int(limit)]


def _set_missing(source, target):
	target.run_method("set_defaults")
	target.run_method("calculate_totals")


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
				"doctype": "Sales Bill",
				"field_no_map": ["naming_series", "remarks"],
				"validation": {"docstatus": ["=", 1]},
				"postprocess": set_customer,
			},
			"Quotation Item": {"doctype": "Sales Bill Item", "field_map": {"parent": "quotation"}},
		},
		target_doc,
		_set_missing,
	)


@frappe.whitelist()
def make_from_sales_order(source_name, target_doc=None):
	def update_item(source, target, source_parent):
		target.qty = flt(source.qty) - flt(source.delivered_qty)

	return get_mapped_doc(
		"Sales Order",
		source_name,
		{
			"Sales Order": {
				"doctype": "Sales Bill",
				"field_map": {"set_warehouse": "warehouse"},
				"field_no_map": ["naming_series", "remarks"],
				"validation": {"docstatus": ["=", 1]},
			},
			"Sales Order Item": {
				"doctype": "Sales Bill Item",
				"field_map": {"parent": "sales_order", "name": "so_detail"},
				"postprocess": update_item,
				"condition": lambda d: flt(d.qty) > flt(d.delivered_qty) and not d.delivered_by_supplier,
			},
		},
		target_doc,
		_set_missing,
	)
