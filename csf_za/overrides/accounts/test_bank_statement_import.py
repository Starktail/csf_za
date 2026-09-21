# import mock
# from mock import patch

from unittest.mock import MagicMock

import frappe
from erpnext import get_default_company
from frappe.tests.utils import FrappeTestCase

default_company = get_default_company()
default_bank = "Test Bank"
default_bank_account = "Checking Account"


class TestCustomBankStatementImport(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()  # important to call super() methods when extending TestCase.

	def setUp(self):
		create_bank_account()

	def test_modify_uploaded_bank_statement_function_runs_on_validate(self):
		bank_statement_import = make_bank_statement_import_test_record(do_not_save=True, do_not_submit=True)
		bank_statement_import.modify_uploaded_bank_statement = MagicMock()
		bank_statement_import.save()
		assert bank_statement_import.modify_uploaded_bank_statement.called


def get_bank_group_account(company):
	"""
	The Bank group account, resolved per company — its abbreviation suffix differs per site
	"""
	return frappe.db.get_value(
		"Account",
		{"company": company, "is_group": 1, "account_type": "Bank"},
		"name",
		order_by="lft asc",
	)


def create_bank_account(bank_name=default_bank, account_name="_Test Bank", company=default_company):
	try:
		gl_account = frappe.get_doc(
			{
				"doctype": "Account",
				"company": company,
				"account_name": account_name,
				"parent_account": get_bank_group_account(company),
				"account_number": "1",
			}
		).insert(ignore_if_duplicate=True)
	except frappe.DuplicateEntryError:
		pass

	try:
		frappe.get_doc(
			{
				"doctype": "Bank",
				"bank_name": bank_name,
			}
		).insert(ignore_if_duplicate=True)
	except frappe.DuplicateEntryError:
		pass

	try:
		bank_account_doc = frappe.get_doc(
			{
				"doctype": "Bank Account",
				"account_name": default_bank_account,
				"bank": bank_name,
				"account": gl_account.name,
				"is_company_account": 1,
				"company": company,
			}
		).insert(ignore_if_duplicate=True)
	except frappe.DuplicateEntryError:
		pass
	except frappe.ValidationError:
		bank_account_doc = frappe.get_all(
			"Bank Account",
			{
				"account_name": default_bank_account,
				"bank": bank_name,
				"account": gl_account.name,
				"company": company,
			},
		)[0]

	return bank_account_doc


def make_bank_statement_import_test_record(**args):
	args = frappe._dict(args)

	bank_statement_import = frappe.new_doc("Bank Statement Import")
	bank_statement_import.company = args.company or default_company
	bank_statement_import.bank = args.bank or default_bank
	bank_statement_import.bank_account = args.bank_account or default_bank_account

	if not args.do_not_save:
		bank_statement_import.insert()

		if not args.do_not_submit:
			bank_statement_import.submit()
	return bank_statement_import
