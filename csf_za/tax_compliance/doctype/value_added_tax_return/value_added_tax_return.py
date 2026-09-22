# Copyright (c) 2024, Dirk van der Laarse and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, cint, getdate
from pypika import Case

from csf_za.tax_compliance.doctype.value_added_tax_return_settings.value_added_tax_return_settings import (
	VAT_RETURN_SETTING_FIELD_MAP,
)

# SARS allows input tax to be claimed up to five years after the tax period it belongs to
DEFAULT_DEFERRAL_LOOKBACK_MONTHS = 60


class ValueaddedTaxReturn(Document):
	TAX_RATE = 15

	def validate(self):
		"""
		Called on save
		"""
		self.refresh_output_tax_fields()
		self.refresh_input_tax_fields()

	def refresh_output_tax_fields(self):
		"""
		Recalculate output tax calculated fields
		"""
		# Calculate field 1
		self.standard_rate_main_excl = sum(
			[
				row.incl_tax_amount
				for row in self.gl_entries
				if row.classification == "Output - A Standard rate (excl capital goods)"
			]
		)

		# Calculate field 4
		self.standard_rate_main_incl = sum(
			[
				row.tax_amount
				for row in self.gl_entries
				if row.classification == "Output - A Standard rate (excl capital goods)"
			]
		)

		# Calculate field 1a
		self.standard_rate_capital_excl = sum(
			[
				row.incl_tax_amount
				for row in self.gl_entries
				if row.classification == "Output - B Standard rate (only capital goods)"
			]
		)

		# Calculate field 4a
		self.stardard_rate_total = sum(
			[
				row.tax_amount
				for row in self.gl_entries
				if row.classification == "Output - B Standard rate (only capital goods)"
			]
		)

		# Calculate field 2
		self.zero_rate_main_excl = sum(
			[
				row.incl_tax_amount
				for row in self.gl_entries
				if row.classification == "Output - C Zero Rated (excl goods exported)"
			]
		)

		# Calculate field 2a
		self.zero_rate_exported_excl = sum(
			[
				row.incl_tax_amount
				for row in self.gl_entries
				if row.classification == "Output - D Zero Rated (only goods exported)"
			]
		)

		# Calculate field 3
		self.exempt_excl = sum(
			[row.incl_tax_amount for row in self.gl_entries if row.classification == "Output - E Exempt"]
		)

		# Calculate field 6
		self.acc_exceed_28_days_total = self.acc_exceed_28_days * float(self.acc_exceed_28_days_percent) / 100

		# Calculate field 8
		self.acc_total_excl = self.acc_exceed_28_days_total + self.acc_not_exceed_28_days

		# Calculate field 9
		self.acc_total_incl = self.acc_total_excl * self.TAX_RATE / 100

		# Calculate field 11
		self.adj_change_in_use_incl = self.adj_change_in_use_excl * self.TAX_RATE / (100 + self.TAX_RATE)

		# Calculate total output tax
		self.total_output_tax = (
			self.standard_rate_main_incl
			+ self.stardard_rate_total
			+ self.acc_total_incl
			+ self.adj_change_in_use_incl
			+ self.adj_other_incl
		)

	def refresh_input_tax_fields(self):
		"""
		Recalculate input tax calculated fields
		"""
		# Calculate field 14
		self.capital_goods_supplied = sum(
			[
				row.tax_amount
				for row in self.gl_entries
				if row.classification == "Input - A Capital goods and/or services supplied to you (local)"
			]
		)

		# Calculate field 14a
		self.capital_goods_imported = sum(
			[
				row.tax_amount
				for row in self.gl_entries
				if row.classification == "Input - B Capital goods imported"
			]
		)

		# Calculate field 15
		self.other_goods_supplied = sum(
			[
				row.tax_amount
				for row in self.gl_entries
				if row.classification == "Input - C Other goods supplied to you (excl capital goods)"
			]
		)

		# Calculate field 15a
		self.other_goods_imported = sum(
			[
				row.tax_amount
				for row in self.gl_entries
				if row.classification == "Input - D Other goods imported (excl capital goods)"
			]
		)

		# Calculate total input tax
		self.total_input_tax = (
			self.capital_goods_supplied
			+ self.capital_goods_imported
			+ self.other_goods_supplied
			+ self.other_goods_imported
			+ self.change_in_use
			+ self.bad_debts
			+ self.other
		)

		# Calculate final amount
		self.total_vat_payable_refundable = self.total_output_tax - self.total_input_tax

	def on_submit(self):
		"""
		Validate when document is submitted
		"""
		unclassified = [row for row in self.gl_entries if not row.classification and not row.is_cancelled]
		if len(unclassified) > 0:
			frappe.throw(
				_("Please classify the {0} remaining unclassified transactions before submitting").format(
					len(unclassified)
				)
			)

	@property
	def deferral_lookback_months(self):
		"""
		Number of months a deferral sweep reaches back before Date from
		"""
		vat_return_settings = frappe.get_cached_doc("Value-added Tax Return Settings", self.company)
		return cint(vat_return_settings.deferral_lookback_months) or DEFAULT_DEFERRAL_LOOKBACK_MONTHS

	@property
	def query_date_from(self):
		"""
		Lower bound of the transaction query, widened when deferred transactions are included
		"""
		if not self.include_previous_period_transactions:
			return self.date_from

		return add_months(getdate(self.date_from), -self.deferral_lookback_months)

	def get_returned_vouchers(self):
		"""
		Vouchers already reported on a submitted return for this company
		"""
		gl_entry_row = frappe.qb.DocType("Value-added Tax Return GL Entry")
		tax_return = frappe.qb.DocType("Value-added Tax Return")

		rows = (
			frappe.qb.from_(gl_entry_row)
			.join(tax_return)
			.on(tax_return.name == gl_entry_row.parent)
			.select(gl_entry_row.voucher_type, gl_entry_row.voucher_no)
			.distinct()
			.where((tax_return.company == self.company) & (tax_return.docstatus == 1))
		).run()

		return set(rows)

	def exclude_previously_returned(self, gl_entries):
		"""
		Drop prior-period rows whose voucher is already on a submitted return
		"""
		if not self.include_previous_period_transactions:
			return gl_entries

		returned_vouchers = self.get_returned_vouchers()
		date_from = getdate(self.date_from)

		return [
			entry
			for entry in gl_entries
			if getdate(entry.posting_date) >= date_from
			or (entry.voucher_type, entry.voucher_no) not in returned_vouchers
		]

	@frappe.whitelist()
	def get_gl_entries(self):
		"""
		Retrieve journal entries for linked accounts
		"""
		vat_return_settings = frappe.get_cached_doc("Value-added Tax Return Settings", self.company)

		gle = frappe.qb.DocType("GL Entry")
		je = frappe.qb.DocType("Journal Entry")
		jea = frappe.qb.DocType("Journal Entry Account")
		si = frappe.qb.DocType("Sales Invoice")
		sitc = frappe.qb.DocType("Sales Taxes and Charges")
		pi = frappe.qb.DocType("Purchase Invoice")
		pitc = frappe.qb.DocType("Purchase Taxes and Charges")
		expense_claims_available = frappe.db.table_exists("Expense Claim")
		if expense_claims_available:
			ec = frappe.qb.DocType("Expense Claim")
			ectc = frappe.qb.DocType("Expense Taxes and Charges")

		tax_accounts = [row.account for row in vat_return_settings.tax_accounts]

		classified_accounts = frappe.get_all(
			"Account",
			filters={"company": self.company},
			or_filters=[
				["custom_vat_return_debit_classification", "not in", ["", None]],
				["custom_vat_return_credit_classification", "not in", ["", None]],
			],
			pluck="name",
		)

		account_condition = gle.account.isin(tax_accounts) | (
			(gle.voucher_type == "Sales Invoice")
			& si.taxes_and_charges.isnotnull()
			& si.debit_to.isnotnull()
			& (gle.account == si.debit_to)
			# Exclude Purchase Invoices with 0 tax for now
			# | (
			# 	(gle.voucher_type == "Purchase Invoice")
			# 	& pi.taxes_and_charges.isnotnull()
			# 	& pi.credit_to.isnotnull()
			# 	& (gle.account == pi.credit_to)
			# )
		)

		if classified_accounts:
			account_condition = account_condition | (
				(gle.voucher_type == "Journal Entry") & gle.account.isin(classified_accounts)
			)

		query = (
			frappe.qb.from_(gle)
			.left_join(je)
			.on(je.name == gle.voucher_no)
			.left_join(jea)
			.on(jea.parent == je.name)
			.left_join(si)
			.on(
				(gle.voucher_type == "Sales Invoice")
				& (si.name == gle.voucher_no)
				& (si.company == self.company)
			)
			.left_join(sitc)
			.on((sitc.parent == si.name) & (sitc.account_head == gle.account))
			.left_join(pi)
			.on(
				(gle.voucher_type == "Purchase Invoice")
				& (pi.name == gle.voucher_no)
				& (pi.company == self.company)
			)
			.left_join(pitc)
			.on((pitc.parent == pi.name) & (pitc.account_head == gle.account))
			.select(
				gle.name,
				gle.account,
				gle.voucher_type,
				gle.voucher_no,
				gle.posting_date,
				gle.is_cancelled,
				gle.debit.as_("general_ledger_debit"),
				gle.credit.as_("general_ledger_credit"),
				je.total_debit.as_("journal_entry_total_debit"),
				je.total_credit.as_("journal_entry_total_credit"),
				je.docstatus.as_("journal_entry_docstatus"),
				jea.account.as_("journal_entry_account"),
				jea.debit.as_("journal_entry_account_debit"),
				jea.credit.as_("journal_entry_account_credit"),
				jea.idx.as_("journal_entry_account_idx"),
				sitc.tax_amount.as_("sales_invoice_taxes_tax_amount"),
				sitc.total.as_("sales_invoice_taxes_total"),
				pitc.tax_amount.as_("purchase_invoice_taxes_tax_amount"),
				pitc.total.as_("purchase_invoice_taxes_total"),
				Case()
				.when(gle.voucher_type == "Sales Invoice", si.taxes_and_charges)
				.when(gle.voucher_type == "Purchase Invoice", pi.taxes_and_charges)
				.else_(None)
				.as_("taxes_and_charges_template"),
			)
			.where(
				(gle.company == self.company)
				& (gle.posting_date >= self.query_date_from)
				& (gle.posting_date <= self.date_to)
				& account_condition
			)
		)

		if expense_claims_available:
			query = (
				query.left_join(ec)
				.on(
					(gle.voucher_type == "Expense Claim")
					& (ec.name == gle.voucher_no)
					& (ec.company == self.company)
				)
				.left_join(ectc)
				.on((ectc.parent == ec.name) & (ectc.account_head == gle.account))
				.select(
					ectc.tax_amount.as_("expense_claim_taxes_tax_amount"),
					ectc.total.as_("expense_claim_taxes_total"),
					ec.grand_total.as_("expense_claim_grand_total"),
				)
			)

		result = self.exclude_previously_returned(query.run(as_dict=True))

		return self.process_gl_entries(result, classified_accounts=classified_accounts)

	def process_gl_entries(self, gl_entries, classified_accounts=None):
		"""
		Perform classification for each journal entry:
		        - If it's linked to a Sales Invoice or Purchase Invoice, get the Taxes and Charges Template
		          and determine the classification based on the maps in Value-add Tax Return Settings
		        - Else, determine the VAT component and infer the classification based on the G/L Account settings
		"""
		vat_return_settings = frappe.get_cached_doc("Value-added Tax Return Settings", self.company)
		tax_accounts = [row.account for row in vat_return_settings.tax_accounts]

		if classified_accounts is None:
			classified_accounts = frappe.get_all(
				"Account",
				filters={"company": self.company},
				or_filters=[
					["custom_vat_return_debit_classification", "not in", ["", None]],
					["custom_vat_return_credit_classification", "not in", ["", None]],
				],
				pluck="name",
			)

		taxes_and_charges_map = [
			entry for entry in VAT_RETURN_SETTING_FIELD_MAP if vat_return_settings.get(entry["field_name"])
		]

		vouchers = transform_gl_entries(gl_entries, tax_accounts, classified_accounts)

		for _voucher_no, item in vouchers.items():
			voucher = item.voucher

			# Skip Cancelled GL Entries
			if voucher.is_cancelled:
				continue

			if hasattr(voucher, "account") and voucher.account in tax_accounts:
				voucher.tax_amount = voucher.general_ledger_debit or voucher.general_ledger_credit
			else:
				voucher.tax_amount = 0

			voucher.classification_debugging = "🚀"

			if voucher.voucher_type in ("Sales Invoice", "Purchase Invoice"):
				voucher.incl_tax_amount = (
					voucher.sales_invoice_taxes_total or voucher.purchase_invoice_taxes_total
				)

				# For vouchers with zero-rated tax (aka tax amount = 0), set the total amount
				# from the control account leg. Net debit against credit so that reversals
				# (Credit and Debit Notes) keep a negative sign
				if not voucher.incl_tax_amount:
					debit = voucher.general_ledger_debit or 0
					credit = voucher.general_ledger_credit or 0
					if voucher.voucher_type == "Sales Invoice":
						voucher.incl_tax_amount = debit - credit
					else:
						voucher.incl_tax_amount = credit - debit

				# If the voucher_type is a reversal (e.g. Credit and Debit Notes, change the sign of tax_amount)
				if voucher.incl_tax_amount < 0 and voucher.tax_amount > 0:
					voucher.tax_amount = voucher.tax_amount * -1

				voucher.classification_debugging += (
					"\n🚀 voucher_type is a 'Sales Invoice' or 'Purchase Invoice'"
				)
				voucher.classification_debugging += (
					f"\n🚀 taxes_and_charges_template = '{voucher.taxes_and_charges_template}'"
				)
				if voucher.taxes_and_charges_template:
					# Find the corresponding field name for the voucher's Taxes and Charges Template
					settings_field = next(
						(
							field
							for field in taxes_and_charges_map
							if field["reference_doctype"] == voucher.voucher_type
							and vat_return_settings.get(field["field_name"])
							== voucher.taxes_and_charges_template
						),
						None,
					)

					voucher.classification_debugging += f"\n🚀 settings_field = {settings_field}"

					# Get the corresponding classification for this field_name
					if settings_field:
						voucher.classification_debugging += (
							f"\n🚀 classification = {settings_field['classification']}"
						)

						voucher.classification = settings_field["classification"]
						continue
				else:
					voucher.classification_debugging += "\n🚀 No Taxes and Charges template on Invoice, or Taxes and Charges template is not set in 'Value-added Return Settings'"

			elif voucher.voucher_type == "Expense Claim":
				voucher.incl_tax_amount = (
					voucher.expense_claim_taxes_total
					or voucher.expense_claim_grand_total
					or voucher.general_ledger_debit
					or voucher.general_ledger_credit
				)

				if voucher.incl_tax_amount and voucher.incl_tax_amount < 0 and voucher.tax_amount > 0:
					voucher.tax_amount = voucher.tax_amount * -1

				expense_types = frappe.get_all(
					"Expense Claim Detail",
					filters={"parent": voucher.voucher_no},
					pluck="expense_type",
				)
				classifications = set()
				for expense_type in expense_types:
					default_account = frappe.db.get_value(
						"Expense Claim Account",
						{"parent": expense_type, "company": self.company},
						"default_account",
					)
					if default_account:
						classification = frappe.get_cached_value(
							"Account",
							default_account,
							"custom_vat_return_debit_classification",
						)
						if classification:
							classifications.add(classification)

				if len(classifications) == 1:
					voucher.classification = classifications.pop()

				voucher.classification_debugging += (
					f"\n🚀 voucher_type is 'Expense Claim'"
					f"\n🚀 expense_claim_taxes_total = {voucher.expense_claim_taxes_total}"
					f"\n🚀 expense_claim_grand_total  = {voucher.expense_claim_grand_total}"
					f"\n🚀 incl_tax_amount            = {voucher.incl_tax_amount}"
					f"\n🚀 expense_types              = {expense_types}"
					f"\n🚀 classifications found      = {classifications}"
					f"\n🚀 classification             = '{voucher.classification}'"
				)

				if voucher.classification:
					continue

			if voucher.voucher_type == "Journal Entry":
				voucher.classification_debugging += "\n🚀 voucher_type is 'Journal Entry'"

				# Exempt / no-VAT Journal Entry: the voucher is a classified non-tax account entry
				# (e.g. Interest Received with custom_vat_return_credit_classification = "Output - E Exempt").
				# tax_amount is already 0. Derive classification and incl_tax_amount from the GL Entry.
				if voucher.account not in tax_accounts:
					amount = voucher.general_ledger_debit or voucher.general_ledger_credit or 0
					voucher.incl_tax_amount = amount
					if voucher.general_ledger_debit:
						voucher.classification = frappe.get_cached_value(
							"Account",
							voucher.account,
							"custom_vat_return_debit_classification",
						)
					else:
						voucher.classification = frappe.get_cached_value(
							"Account",
							voucher.account,
							"custom_vat_return_credit_classification",
						)
					voucher.classification_debugging += (
						f"\n🚀 [exempt JE] account='{voucher.account}'"
						f"\n🚀 incl_tax_amount={voucher.incl_tax_amount}"
						f"\n🚀 classification='{voucher.classification}'"
					)
					continue

				# Process pairs of Journal Entry Account child records
				# E.g.
				#
				# |   | account          | debit | credit |
				# |---|------------------|-------|--------|
				# | 1 | interest         | 1000  | 0      |
				# | 2 | bank             | 0     | 1000   |
				# | 3 | fees and charges | 100   |        |
				# | 4 | vat              | 15    | 0      |
				# | 5 | bank             | 0     | 115    |
				#
				# Here we want to ignore transactions that has nothing to do with VAT.
				# Thus, rows 1 and 2 should be filtered out.

				filtered_out = []
				for journal_entry in item.linked_journal_entries:
					if journal_entry not in filtered_out:
						contra_entry_with_same_amount = None
						if journal_entry.journal_entry_account_debit != 0:
							debit_amount = journal_entry.journal_entry_account_debit
							contra_entry_with_same_amount = next(
								(
									je
									for je in item.linked_journal_entries
									if je.journal_entry_account_credit == debit_amount
								),
								None,
							)
						elif journal_entry.journal_entry_account_credit != 0:
							credit_amount = journal_entry.journal_entry_account_credit
							contra_entry_with_same_amount = next(
								(
									je
									for je in item.linked_journal_entries
									if je.journal_entry_account_debit == credit_amount
								),
								None,
							)
						if contra_entry_with_same_amount:
							filtered_out += [
								contra_entry_with_same_amount,
								journal_entry,
							]

				filtered_journal_entries = [
					je_entry for je_entry in item.linked_journal_entries if je_entry not in filtered_out
				]

				if len(filtered_journal_entries) == 0 and len(filtered_out) > 0:
					voucher.classification = "SARS Payment/Receipt"
					continue

				voucher.classification_debugging += (
					f"\n🚀 filtered_out = rows {[je.journal_entry_account_idx for je in filtered_out]}"
				)
				voucher.classification_debugging += f"\n🚀 filtered_journal_entries = rows {[je.journal_entry_account_idx for je in filtered_journal_entries]}"

				# The voucher is the tax leg (guaranteed by transform_gl_entries).
				# Isolate this GL Entry's JEA row, other tax-account JEA rows, and non-tax JEA rows.
				this_gl_debit = voucher.general_ledger_debit or 0
				this_gl_credit = voucher.general_ledger_credit or 0

				this_tax_jea = next(
					(
						je
						for je in filtered_journal_entries
						if je.journal_entry_account == voucher.account
						and abs((je.journal_entry_account_debit or 0) - this_gl_debit) < 0.01
						and abs((je.journal_entry_account_credit or 0) - this_gl_credit) < 0.01
					),
					None,
				)
				other_tax_jea_rows = [
					je
					for je in filtered_journal_entries
					if je.journal_entry_account in tax_accounts and je is not this_tax_jea
				]
				non_tax_entries = [
					je
					for je in filtered_journal_entries
					if je not in other_tax_jea_rows and je is not this_tax_jea
				]

				voucher.classification_debugging += f"\n🚀 this_tax_jea idx = {this_tax_jea.journal_entry_account_idx if this_tax_jea else 'None'}"
				voucher.classification_debugging += (
					f"\n🚀 other_tax_jea rows = {[je.journal_entry_account_idx for je in other_tax_jea_rows]}"
				)
				voucher.classification_debugging += (
					f"\n🚀 non_tax_entries = {[je.journal_entry_account_idx for je in non_tax_entries]}"
				)

				# Scenario 1: write-off pattern — 1 non-tax debit + multiple credits (or vice versa).
				# Also handles standard 3-leg JEs (1 expense debit + 1 offset credit).
				if this_gl_debit:
					other_debits = [je for je in non_tax_entries if je.journal_entry_account_debit != 0]
					other_credits = [je for je in non_tax_entries if je.journal_entry_account_credit != 0]
					if len(other_debits) == 1 and other_credits:
						excl_tax_leg = other_debits[0]
						voucher.incl_tax_amount = sum(c.journal_entry_account_credit for c in other_credits)
						voucher.classification = frappe.get_cached_value(
							"Account",
							excl_tax_leg.journal_entry_account,
							"custom_vat_return_debit_classification",
						)
						voucher.classification_debugging += (
							f"\n🚀 [strategy 1] excl_tax_leg = '{excl_tax_leg.journal_entry_account}'"
							f"\n🚀 classification = '{voucher.classification}'"
						)
						continue

				elif this_gl_credit:
					other_debits = [je for je in non_tax_entries if je.journal_entry_account_debit != 0]
					other_credits = [je for je in non_tax_entries if je.journal_entry_account_credit != 0]
					if len(other_credits) == 1 and other_debits:
						excl_tax_leg = other_credits[0]
						voucher.incl_tax_amount = sum(d.journal_entry_account_debit for d in other_debits)
						voucher.classification = frappe.get_cached_value(
							"Account",
							excl_tax_leg.journal_entry_account,
							"custom_vat_return_credit_classification",
						)
						voucher.classification_debugging += (
							f"\n🚀 [strategy 1] excl_tax_leg = '{excl_tax_leg.journal_entry_account}'"
							f"\n🚀 classification = '{voucher.classification}'"
						)
						continue

				# Scenario 2: adjacent-index pairing — find the non-tax JEA row immediately
				# before (or after) this tax leg by index order. Used when multiple expense
				# accounts exist in the same JE (multiple VAT legs).
				if this_tax_jea and non_tax_entries:
					this_idx = this_tax_jea.journal_entry_account_idx or 0
					non_tax_sorted = sorted(
						non_tax_entries,
						key=lambda je: je.journal_entry_account_idx or 0,
					)
					if this_gl_debit:
						preceding = [
							je
							for je in non_tax_sorted
							if (je.journal_entry_account_idx or 0) < this_idx
							and je.journal_entry_account_debit != 0
						]
						following = [
							je
							for je in non_tax_sorted
							if (je.journal_entry_account_idx or 0) > this_idx
							and je.journal_entry_account_debit != 0
						]
					else:
						preceding = [
							je
							for je in non_tax_sorted
							if (je.journal_entry_account_idx or 0) < this_idx
							and je.journal_entry_account_credit != 0
						]
						following = [
							je
							for je in non_tax_sorted
							if (je.journal_entry_account_idx or 0) > this_idx
							and je.journal_entry_account_credit != 0
						]
					adjacent = (preceding[-1] if preceding else None) or (following[0] if following else None)
					if adjacent:
						excl_amount = (
							adjacent.journal_entry_account_debit or adjacent.journal_entry_account_credit or 0
						)
						voucher.incl_tax_amount = excl_amount + (this_gl_debit or this_gl_credit)
						if this_gl_debit:
							voucher.classification = frappe.get_cached_value(
								"Account",
								adjacent.journal_entry_account,
								"custom_vat_return_debit_classification",
							)
						else:
							voucher.classification = frappe.get_cached_value(
								"Account",
								adjacent.journal_entry_account,
								"custom_vat_return_credit_classification",
							)
						voucher.classification_debugging += (
							f"\n🚀 [strategy 2] adjacent idx={adjacent.journal_entry_account_idx}"
							f" account='{adjacent.journal_entry_account}'"
							f"\n🚀 classification = '{voucher.classification}'"
						)
						continue

				# Scenario 3: min/max heuristic — fallback for entries where secanrios 1 and 2
				# did not resolve (e.g. no clear single-debit / single-credit pattern).
				incl_tax_leg = None
				excl_tax_leg = None
				try:
					incl_tax_leg = max(
						non_tax_entries,
						key=lambda je: abs(je.journal_entry_account_credit or je.journal_entry_account_debit),
					)
					excl_tax_leg = min(
						non_tax_entries,
						key=lambda je: abs(je.journal_entry_account_credit or je.journal_entry_account_debit),
					)
				except (ValueError, TypeError) as e:
					voucher.classification_debugging += f"\n🚀 {e}"

				if incl_tax_leg and excl_tax_leg:
					if excl_tax_leg.journal_entry_account_debit != 0:
						voucher.classification = frappe.get_cached_value(
							"Account",
							excl_tax_leg.journal_entry_account,
							"custom_vat_return_debit_classification",
						)
						voucher.incl_tax_amount = (
							incl_tax_leg.journal_entry_account_credit
							or incl_tax_leg.journal_entry_account_debit
						)
						voucher.classification_debugging += (
							f"\n🚀 [strategy 3] excl_tax_leg = '{excl_tax_leg.journal_entry_account}'"
							f"\n🚀 classification = '{voucher.classification}'"
						)
						continue
					elif excl_tax_leg.journal_entry_account_credit != 0:
						voucher.classification = frappe.get_cached_value(
							"Account",
							excl_tax_leg.journal_entry_account,
							"custom_vat_return_credit_classification",
						)
						voucher.incl_tax_amount = (
							incl_tax_leg.journal_entry_account_credit
							or incl_tax_leg.journal_entry_account_debit
						)
						voucher.classification_debugging += (
							f"\n🚀 [strategy 3] excl_tax_leg = '{excl_tax_leg.journal_entry_account}'"
							f"\n🚀 classification = '{voucher.classification}'"
						)
						continue

		return [voucher.voucher for voucher in vouchers.values()]


def transform_gl_entries(gl_entries, tax_accounts, classified_accounts=None):
	"""
	Transform flat list of GL Entry rows into a dict of vouchers.

	For Journal Entries: one entry per tax-account GL Entry (keyed by gle.name).
	All entries for the same JE share a deduplicated linked_journal_entries list.
	For Sales/Purchase Invoices: one entry per voucher_no (existing behaviour).
	"""
	tax_accounts_set = set(tax_accounts)
	classified_accounts_set = set(classified_accounts or [])

	# Pass 1: collect unique JEA rows per JE voucher_no, deduplicated by idx.
	# The query cross-joins every GLE row with every JEA row, producing duplicates.
	je_jea_rows = {}  # {voucher_no: {idx: entry}}
	for entry in gl_entries:
		if entry.voucher_type == "Journal Entry":
			vno = entry.voucher_no
			idx = entry.journal_entry_account_idx
			if vno not in je_jea_rows:
				je_jea_rows[vno] = {}
			if idx not in je_jea_rows[vno]:
				je_jea_rows[vno][idx] = entry

	# Pass 2: build the vouchers dict.
	vouchers = {}
	je_has_tax_leg = set()  # voucher_nos that have at least one tax-account GL Entry
	je_classified_non_tax = {}  # {voucher_no: [GL Entry rows for classified non-tax accounts]}
	for entry in gl_entries:
		vno = entry.voucher_no
		if entry.voucher_type == "Journal Entry":
			if entry.account in tax_accounts_set:
				je_has_tax_leg.add(vno)
				key = entry.name
				if key not in vouchers:
					linked = list(je_jea_rows.get(vno, {}).values())
					vouchers[key] = frappe._dict({"voucher": entry, "linked_journal_entries": linked})
			elif entry.account in classified_accounts_set:
				je_classified_non_tax.setdefault(vno, []).append(entry)
		else:
			if vno not in vouchers:
				vouchers[vno] = frappe._dict({"voucher": entry, "linked_journal_entries": []})
			else:
				if (
					hasattr(entry, "account")
					and hasattr(vouchers[vno].voucher, "account")
					and entry.account in tax_accounts_set
					and vouchers[vno].voucher.account not in tax_accounts_set
				):
					vouchers[vno].voucher = entry
			vouchers[vno]["linked_journal_entries"].append(entry)

	# Pass 3: for JEs with no tax leg, add each classified non-tax entry as its own voucher.
	# JEs that already have a tax leg are handled above — skip them to avoid double-counting.
	for vno, entries in je_classified_non_tax.items():
		if vno not in je_has_tax_leg:
			for entry in entries:
				key = entry.name
				if key not in vouchers:
					linked = list(je_jea_rows.get(vno, {}).values())
					vouchers[key] = frappe._dict({"voucher": entry, "linked_journal_entries": linked})

	return vouchers
