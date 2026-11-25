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

	def test_unprepared_return_with_ledger_activity_is_blocked(self):
		self.create_journal_entry(IN_PERIOD)
		vat_return = self.create_return(CURRENT_PERIOD)

		self.assertRaisesRegex(frappe.ValidationError, "Retrieve transactions", vat_return.submit)

	def test_nil_return_submits(self):
		self.create_journal_entry("2025-10-01")
		vat_return = self.create_return(CURRENT_PERIOD)

		vat_return.submit()

		self.assertEqual(vat_return.docstatus, 1)

	def test_empty_return_with_only_cancelled_activity_submits(self):
		self.create_journal_entry(IN_PERIOD).cancel()
		vat_return = self.create_return(CURRENT_PERIOD)

		vat_return.submit()

		self.assertEqual(vat_return.docstatus, 1)

	def test_summary_caps_the_list_and_reports_the_true_total(self):
		vat_return = self.create_return(CURRENT_PERIOD)
		late_entries = [
			self.create_journal_entry(IN_PERIOD) for _index in range(LateEntryCheck.LIST_LIMIT + 1)
		]

		summary = vat_return.get_late_entry_summary()

		self.assertEqual(summary["total"], LateEntryCheck.LIST_LIMIT + 1)
		self.assertIn(late_entries[-2].name, summary["html"])
		self.assertNotIn(late_entries[-1].name, summary["html"])
		self.assertIn("and 1 more", summary["html"])

	def test_summary_without_late_vouchers_has_no_html(self):
		vat_return = self.prepared_return()

		self.assertEqual(vat_return.get_late_entry_summary(), {"checked": True, "total": 0})

	def test_summary_reports_missing_settings_rather_than_throwing(self):
		vat_return = self.create_return(CURRENT_PERIOD)
		frappe.delete_doc("Value-added Tax Return Settings", self.company)

		self.assertEqual(
			vat_return.get_late_entry_summary(), {"checked": False, "reason": "settings_missing"}
		)

	def test_late_gl_entries_cover_every_late_voucher_and_leg(self):
		vat_return = self.create_return(CURRENT_PERIOD)
		captured = self.create_journal_entry(IN_PERIOD)
		self.populate(vat_return)
		late_entries = [self.create_journal_entry(IN_PERIOD) for _index in range(LateEntryCheck.LIST_LIMIT)]
		two_legs = self.create_journal_entry(IN_PERIOD, vat_lines=2)

		voucher_numbers = [row.voucher_no for row in vat_return.get_late_gl_entries()]

		self.assertNotIn(captured.name, voucher_numbers)
		self.assertEqual(set(voucher_numbers), {entry.name for entry in [*late_entries, two_legs]})
		self.assertEqual(voucher_numbers.count(two_legs.name), 2)
