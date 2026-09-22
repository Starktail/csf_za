# Copyright (c) 2024, Dirk van der Laarse and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_months, getdate

from csf_za.tax_compliance.doctype.value_added_tax_return.test_value_added_tax_return import (
	create_account,
)
from csf_za.tax_compliance.doctype.value_added_tax_return.value_added_tax_return import (
	DEFAULT_DEFERRAL_LOOKBACK_MONTHS,
)

test_dependencies = ["Value-added Tax Return"]

PRIOR_PERIOD = {
	"tax_period": "202508",
	"date_from": "2025-08-01",
	"date_to": "2025-08-31",
}
CURRENT_PERIOD = {
	"tax_period": "202509",
	"date_from": "2025-09-01",
	"date_to": "2025-09-30",
}


class TestValueaddedTaxReturnDeferral(FrappeTestCase):
	def setUp(self):
		frappe.db.delete("GL Entry")
		frappe.db.delete("Journal Entry")
		frappe.db.delete("Value-added Tax Return GL Entry")
		frappe.db.delete("Value-added Tax Return")

		self.company = "_Test Company"

		self.vat_account = create_account("VAT Test", "Tax Assets - _TC", self.company, account_type="Tax")
		self.bank_account = create_account(
			"Bank Test", "Current Assets - _TC", self.company, account_type="Bank"
		)
		self.expense_account = create_account(
			"Deferral Expense Test",
			"Direct Expenses - _TC",
			self.company,
			account_type="Expense Account",
		)
		self.expense_account.custom_vat_return_debit_classification = (
			"Input - C Other goods supplied to you (excl capital goods)"
		)
		self.expense_account.save()

		self.setup_settings()

	def setup_settings(self):
		"""
		Point the settings at the test VAT account and give the sweep a predictable depth
		"""
		if not frappe.db.exists("Value-added Tax Return Settings", self.company):
			frappe.get_doc(
				{
					"doctype": "Value-added Tax Return Settings",
					"company": self.company,
					"transaction_classification": "Taxes and Charges Templates",
				}
			).insert()

		settings = frappe.get_doc("Value-added Tax Return Settings", self.company)
		settings.tax_accounts = []
		settings.append("tax_accounts", {"account": self.vat_account.name})
		settings.deferral_lookback_months = 12
		settings.save()

	def set_lookback_months(self, months):
		settings = frappe.get_doc("Value-added Tax Return Settings", self.company)
		settings.deferral_lookback_months = months
		settings.save()

	def create_journal_entry(self, posting_date):
		"""
		A submitted VAT-bearing journal the classification engine can classify on its own
		"""
		journal_entry = frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"voucher_type": "Journal Entry",
				"company": self.company,
				"posting_date": posting_date,
				"accounts": [
					{
						"account": self.expense_account.name,
						"debit_in_account_currency": 100,
					},
					{"account": self.vat_account.name, "debit_in_account_currency": 15},
					{
						"account": self.bank_account.name,
						"credit_in_account_currency": 115,
					},
				],
			}
		)
		journal_entry.insert()
		journal_entry.submit()
		return journal_entry

	def create_return(self, period, include_previous_period=0):
		vat_return = frappe.get_doc(
			{
				"doctype": "Value-added Tax Return",
				"company": self.company,
				"include_previous_period_transactions": include_previous_period,
				**period,
			}
		)
		vat_return.insert()
		return vat_return

	def fetch_voucher_numbers(self, vat_return):
		return {entry.get("voucher_no") for entry in vat_return.get_gl_entries()}

	def populate(self, vat_return):
		"""
		Fill the Transactions table the way the client does, so fetch_from columns are stored
		"""
		vat_return.gl_entries = []
		for entry in vat_return.get_gl_entries():
			vat_return.append(
				"gl_entries",
				{
					"gl_entry": entry.get("name"),
					"posting_date": entry.get("posting_date"),
					"voucher_type": entry.get("voucher_type"),
					"voucher_no": entry.get("voucher_no"),
					"classification": entry.get("classification"),
					"tax_amount": entry.get("tax_amount"),
					"incl_tax_amount": entry.get("incl_tax_amount"),
					"is_cancelled": entry.get("is_cancelled"),
				},
			)
		vat_return.save()
		return vat_return

	def create_prior_return_holding(self, journal_entry, docstatus):
		"""
		A prior-period return carrying the journal, left at the requested docstatus
		"""
		vat_return = self.populate(self.create_return(PRIOR_PERIOD))
		self.assertIn(journal_entry.name, {row.voucher_no for row in vat_return.gl_entries})

		if docstatus >= 1:
			vat_return.submit()
		if docstatus == 2:
			vat_return.cancel()

		return vat_return

	def test_prior_period_entry_absent_when_checkbox_unticked(self):
		journal_entry = self.create_journal_entry("2025-08-15")

		vat_return = self.create_return(CURRENT_PERIOD)

		self.assertNotIn(journal_entry.name, self.fetch_voucher_numbers(vat_return))

	def test_prior_period_entry_included_when_checkbox_ticked(self):
		journal_entry = self.create_journal_entry("2025-08-15")

		vat_return = self.create_return(CURRENT_PERIOD, include_previous_period=1)

		self.assertIn(journal_entry.name, self.fetch_voucher_numbers(vat_return))

	def test_voucher_on_submitted_return_is_not_returned_again(self):
		journal_entry = self.create_journal_entry("2025-08-15")
		self.create_prior_return_holding(journal_entry, docstatus=1)

		vat_return = self.create_return(CURRENT_PERIOD, include_previous_period=1)

		self.assertNotIn(journal_entry.name, self.fetch_voucher_numbers(vat_return))

	def test_voucher_on_draft_return_is_still_returned(self):
		journal_entry = self.create_journal_entry("2025-08-15")
		self.create_prior_return_holding(journal_entry, docstatus=0)

		vat_return = self.create_return(CURRENT_PERIOD, include_previous_period=1)

		self.assertIn(journal_entry.name, self.fetch_voucher_numbers(vat_return))

	def test_voucher_on_cancelled_return_is_returned_again(self):
		journal_entry = self.create_journal_entry("2025-08-15")
		self.create_prior_return_holding(journal_entry, docstatus=2)

		vat_return = self.create_return(CURRENT_PERIOD, include_previous_period=1)

		self.assertIn(journal_entry.name, self.fetch_voucher_numbers(vat_return))

	def test_entry_older_than_lookback_window_is_excluded(self):
		self.set_lookback_months(1)
		journal_entry = self.create_journal_entry("2025-06-15")

		vat_return = self.create_return(CURRENT_PERIOD, include_previous_period=1)

		self.assertEqual(vat_return.query_date_from, getdate("2025-08-01"))
		self.assertNotIn(journal_entry.name, self.fetch_voucher_numbers(vat_return))

	def test_current_period_entry_is_never_excluded(self):
		journal_entry = self.create_journal_entry("2025-09-10")
		self.populate(self.create_return(CURRENT_PERIOD)).submit()

		vat_return = self.create_return(
			{**CURRENT_PERIOD, "tax_period": "202509A"}, include_previous_period=1
		)

		self.assertIn(journal_entry.name, self.fetch_voucher_numbers(vat_return))

	def test_lookback_falls_back_to_default_when_setting_blank(self):
		self.set_lookback_months(0)

		vat_return = self.create_return(CURRENT_PERIOD, include_previous_period=1)

		self.assertEqual(vat_return.deferral_lookback_months, DEFAULT_DEFERRAL_LOOKBACK_MONTHS)
		self.assertEqual(
			vat_return.query_date_from,
			add_months(getdate(CURRENT_PERIOD["date_from"]), -DEFAULT_DEFERRAL_LOOKBACK_MONTHS),
		)
