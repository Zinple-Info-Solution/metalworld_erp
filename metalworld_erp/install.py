import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def after_install():
	setup_customer_account_no()


def setup_customer_account_no():
	"""Account No: the customer's account number from the legacy system (A/C No on Sales Bill)."""
	create_custom_fields(
		{
			"Customer": [
				{
					"fieldname": "account_no",
					"fieldtype": "Data",
					"label": "Account No",
					"insert_after": "customer_name",
					"unique": 1,
					"in_standard_filter": 1,
					"in_global_search": 1,
					"no_copy": 1,
				}
			]
		},
		update=True,
	)
	search_fields = frappe.get_meta("Customer").search_fields or ""
	fields = [f.strip() for f in search_fields.split(",") if f.strip()]
	if "account_no" not in fields:
		make_property_setter(
			"Customer",
			None,
			"search_fields",
			", ".join(fields + ["account_no"]),
			"Data",
			for_doctype=True,
			validate_fields_for_doctype=False,
		)
	frappe.clear_cache(doctype="Customer")
