from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

MODULE = "Metalworld Erp"

# Fields that bring the POS Invoice in line with the legacy desktop "Sales" screen.
CUSTOM_FIELDS = {
	"POS Invoice": [
		# Customer column
		{
			"fieldname": "customer_mobile",
			"label": "Mobile No",
			"fieldtype": "Data",
			"options": "Phone",
			"fetch_from": "customer.mobile_no",
			"fetch_if_empty": 1,
			"insert_after": "tax_id",
		},
		{
			"fieldname": "simplified_tax_invoice",
			"label": "Simplified Tax Invoice",
			"fieldtype": "Check",
			"insert_after": "customer_mobile",
		},
		{
			"fieldname": "change_cash_customer_name",
			"label": "Change Cash Customer Name",
			"fieldtype": "Check",
			"no_copy": 1,
			"print_hide": 1,
			"insert_after": "simplified_tax_invoice",
		},
		{
			"fieldname": "cash_customer_name",
			"label": "Cash Customer Name",
			"fieldtype": "Data",
			"no_copy": 1,
			"depends_on": "change_cash_customer_name",
			"mandatory_depends_on": "change_cash_customer_name",
			"insert_after": "change_cash_customer_name",
		},
		# Company column
		{
			"fieldname": "branch",
			"label": "Branch",
			"fieldtype": "Link",
			"options": "Branch",
			"in_standard_filter": 1,
			"remember_last_selected_value": 1,
			"insert_after": "company",
		},
		{
			"fieldname": "sales_type",
			"label": "Type",
			"fieldtype": "Select",
			"options": "Cash\nCredit",
			"default": "Cash",
			"reqd": 1,
			"in_list_view": 1,
			"in_standard_filter": 1,
			"description": "Cash invoices are paid in full on submit; Credit invoices stay outstanding.",
			"depends_on": "eval:!doc.is_return",
			"insert_after": "branch",
		},
		{
			"fieldname": "is_bank",
			"label": "Is Bank",
			"fieldtype": "Check",
			"description": "Collect the payment through the Bank mode of payment instead of Cash.",
			"depends_on": "eval:doc.sales_type == 'Cash' && !doc.is_return",
			"insert_after": "sales_type",
		},
		{
			"fieldname": "ref_no",
			"label": "Ref. No",
			"fieldtype": "Data",
			"no_copy": 1,
			"in_global_search": 1,
			"insert_after": "is_bank",
		},
		# Totals
		{
			"fieldname": "previous_balance",
			"label": "Previous Balance",
			"fieldtype": "Currency",
			"options": "currency",
			"read_only": 1,
			"no_copy": 1,
			"description": "Customer balance before this invoice, including submitted POS Invoices not yet consolidated.",
			"insert_after": "outstanding_amount",
		},
		{
			"fieldname": "total_due",
			"label": "Total Due",
			"fieldtype": "Currency",
			"options": "currency",
			"read_only": 1,
			"no_copy": 1,
			"bold": 1,
			"insert_after": "previous_balance",
		},
	],
	"POS Invoice Item": [
		{
			"fieldname": "model",
			"label": "Model",
			"fieldtype": "Data",
			"insert_after": "item_name",
		},
		{
			"fieldname": "is_foc",
			"label": "FOC",
			"fieldtype": "Check",
			"description": "Free of charge: the line is invoiced at zero rate.",
			"insert_after": "rate",
		},
		{
			"fieldname": "item_cost",
			"label": "Cost",
			"fieldtype": "Currency",
			"options": "Company:company:default_currency",
			"read_only": 1,
			"no_copy": 1,
			"print_hide": 1,
			"report_hide": 1,
			"insert_after": "amount",
		},
		{
			"fieldname": "vat_rate",
			"label": "Tax %",
			"fieldtype": "Percent",
			"read_only": 1,
			"insert_after": "net_amount",
		},
		{
			"fieldname": "vat_amount",
			"label": "Tax Amount",
			"fieldtype": "Currency",
			"options": "currency",
			"read_only": 1,
			"insert_after": "vat_rate",
		},
		{
			"fieldname": "rate_inc_vat",
			"label": "S.Price Inc VAT",
			"fieldtype": "Currency",
			"options": "currency",
			"read_only": 1,
			"in_list_view": 1,
			"columns": 1,
			"insert_after": "vat_amount",
		},
		{
			"fieldname": "amount_inc_vat",
			"label": "Net Inc VAT",
			"fieldtype": "Currency",
			"options": "currency",
			"read_only": 1,
			"in_list_view": 1,
			"columns": 1,
			"insert_after": "rate_inc_vat",
		},
	],
}


def setup():
	for fields in CUSTOM_FIELDS.values():
		for df in fields:
			df.setdefault("module", MODULE)
	create_custom_fields(CUSTOM_FIELDS, update=True)
