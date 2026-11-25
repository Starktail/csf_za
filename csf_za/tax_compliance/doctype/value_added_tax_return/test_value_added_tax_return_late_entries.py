# Copyright (c) 2026, Dirk van der Laarse and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from csf_za.tax_compliance.doctype.value_added_tax_return.late_entries import LateEntryCheck
from csf_za.tax_compliance.doctype.value_added_tax_return.test_value_added_tax_return_deferral import (
	CURRENT_PERIOD,
	ValueAddedTaxReturnFixtures,
)

test_dependencies = ["Value-added Tax Return"]

IN_PERIOD = "2025-09-20"


class TestValueaddedTaxReturnLateEntries(ValueAddedTaxReturnFixtures, FrappeTestCase):
	def setUp(self):
		super().setUp()
		# Raw truncation of the returns leaves their timeline behind, and return names repeat
		frappe.db.delete("Comment", {"reference_doctype": "Value-added Tax Return"})

	def prepared_return(self):
		"""
		A return already holding a voucher posted before it, so it can be submitted
		"""
		self.create_journal_entry("2025-09-05")
		return self.populate(self.create_return(CURRENT_PERIOD))

	def late_voucher_numbers(self, vat_return):
		return [voucher_no for _voucher_type, voucher_no in LateEntryCheck(vat_return).late_vouchers]

	def info_entries(self, vat_return):
		return frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": "Value-added Tax Return",
				"reference_name": vat_return.name,
				"comment_type": "Info",
			},
			pluck="content",
		)

	def test_voucher_posted_after_return_is_flagged(self):
		vat_return = self.create_return(CURRENT_PERIOD)
		journal_entry = self.create_journal_entry(IN_PERIOD)

		self.assertIn(journal_entry.name, self.late_voucher_numbers(vat_return))

	def test_voucher_created_before_return_is_not_flagged(self):
		self.create_journal_entry(IN_PERIOD)
		vat_return = self.create_return(CURRENT_PERIOD)

		self.assertEqual(self.late_voucher_numbers(vat_return), [])

	def test_voucher_already_on_return_is_suppressed(self):
		vat_return = self.create_return(CURRENT_PERIOD)
		self.create_journal_entry(IN_PERIOD)
		self.populate(vat_return)

		self.assertEqual(self.late_voucher_numbers(vat_return), [])

	def test_cancelled_voucher_is_ignored(self):
		vat_return = self.create_return(CURRENT_PERIOD)
		self.create_journal_entry(IN_PERIOD).cancel()

		self.assertEqual(self.late_voucher_numbers(vat_return), [])

	def test_voucher_outside_period_is_ignored(self):
		vat_return = self.create_return(CURRENT_PERIOD)
		self.create_journal_entry("2025-08-31")
		self.create_journal_entry("2025-10-01")

		self.assertEqual(self.late_voucher_numbers(vat_return), [])

	def test_multi_leg_journal_entry_is_flagged_once(self):
		vat_return = self.create_return(CURRENT_PERIOD)
		journal_entry = self.create_journal_entry(IN_PERIOD)

		self.assertGreater(len(LateEntryCheck(vat_return).fetch_late_entries()), 1)
		self.assertEqual(self.late_voucher_numbers(vat_return), [journal_entry.name])

	def test_amendment_flags_voucher_posted_before_it_was_created(self):
		original = self.prepared_return()
		original.submit()
		journal_entry = self.create_journal_entry(IN_PERIOD)
		original.cancel()

		amendment = frappe.copy_doc(original)
		amendment.amended_from = original.name
		amendment.docstatus = 0
		amendment.insert()

		self.assertIn(journal_entry.name, self.late_voucher_numbers(amendment))

	def test_audit_entry_is_info_and_lists_every_late_voucher(self):
		vat_return = self.prepared_return()
		late_entries = [
			self.create_journal_entry(IN_PERIOD) for _index in range(LateEntryCheck.LIST_LIMIT + 1)
		]

		vat_return.submit()

		(content,) = self.info_entries(vat_return)
		self.assertIn("Administrator", content)
		for journal_entry in late_entries:
			self.assertIn(journal_entry.name, content)

	def test_no_entry_when_nothing_is_late(self):
		vat_return = self.prepared_return()

		vat_return.submit()

		self.assertEqual(self.info_entries(vat_return), [])

	def test_missing_settings_record_nothing_and_stay_silent(self):
		vat_return = self.create_return(CURRENT_PERIOD)
		self.create_journal_entry(IN_PERIOD)
		frappe.delete_doc("Value-added Tax Return Settings", self.company)
		frappe.local.message_log = []

		vat_return.submit()

		self.assertEqual(self.info_entries(vat_return), [])
		self.assertEqual(frappe.local.message_log, [])

	def test_settings_without_tax_accounts_record_nothing(self):
		vat_return = self.create_return(CURRENT_PERIOD)
		self.create_journal_entry(IN_PERIOD)
		settings = frappe.get_doc("Value-added Tax Return Settings", self.company)
		settings.tax_accounts = []
		settings.save()

		vat_return.submit()

		self.assertEqual(self.info_entries(vat_return), [])

	def test_unclassified_rows_still_block_submit(self):
		vat_return = self.prepared_return()
		vat_return.gl_entries[0].classification = None
		vat_return.save()

		self.assertRaisesRegex(frappe.ValidationError, "unclassified", vat_return.submit)
