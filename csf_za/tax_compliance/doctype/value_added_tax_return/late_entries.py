# Copyright (c) 2026, Dirk van der Laarse and contributors
# For license information, please see license.txt

from functools import cached_property

import frappe
from frappe import _


class LateEntryCheck:
	"""
	Ledger entries posted into the return's period after the return was first created
	"""

	LIST_LIMIT = 10
	TEMPLATE = "csf_za/tax_compliance/doctype/value_added_tax_return/templates/late_entries.html"

	def __init__(self, vat_return):
		self.vat_return = vat_return

	@property
	def can_run(self):
		"""
		The account condition needs Settings with at least one tax account; an empty IN () is invalid SQL
		"""
		return bool(
			self.vat_return.date_from
			and self.vat_return.date_to
			and frappe.db.exists(
				"Value-added Tax Return Settings Account",
				{"parenttype": "Value-added Tax Return Settings", "parent": self.vat_return.company},
			)
		)

	@cached_property
	def anchor(self):
		"""
		Creation of the first return in the amendment chain, since an amendment inherits its rows
		"""
		name, creation = self.vat_return.amended_from, self.vat_return.creation
		while name:
			name, creation = frappe.db.get_value("Value-added Tax Return", name, ["amended_from", "creation"])
		return creation

	@property
	def captured_vouchers(self):
		return {(row.voucher_type, row.voucher_no) for row in self.vat_return.gl_entries if row.voucher_no}

	@cached_property
	def late_vouchers(self):
		"""
		First ledger row of each late voucher not already on the return, keyed by (voucher_type, voucher_no)
		"""
		captured = self.captured_vouchers
		late = {}
		for entry in self.fetch_late_entries():
			identity = (entry.voucher_type, entry.voucher_no)
			if identity not in captured:
				late.setdefault(identity, entry)
		return late

	def fetch_late_entries(self):
		gle = frappe.qb.DocType("GL Entry")
		return (
			self.ledger_query(gle)
			.where(gle.creation > self.anchor)
			.select(gle.voucher_type, gle.voucher_no, gle.posting_date, gle.owner, gle.creation)
			.orderby(gle.creation)
			.run(as_dict=True)
		)

	def ledger_query(self, gle):
		"""
		VAT-impacting, non-cancelled ledger rows posted into the return's own period
		"""
		si = frappe.qb.DocType("Sales Invoice")
		vat_return = self.vat_return
		return (
			frappe.qb.from_(gle)
			.left_join(si)
			.on((gle.voucher_type == "Sales Invoice") & (si.name == gle.voucher_no))
			.where(
				(gle.company == vat_return.company)
				& gle.posting_date.between(vat_return.date_from, vat_return.date_to)
				& (gle.is_cancelled == 0)
				& vat_return.build_account_condition(gle, si, vat_return.classified_accounts)
			)
		)

	def ledger_has_transactions(self):
		"""
		Whether the period holds any VAT-impacting voucher at all, whenever it was created
		"""
		gle = frappe.qb.DocType("GL Entry")
		return bool(self.ledger_query(gle).select(gle.name).limit(1).run())

	def summary(self):
		"""
		The submit dialog's payload: the true count and the capped voucher table
		"""
		if not self.can_run:
			return {"checked": False, "reason": "settings_missing"}

		summary = {"checked": True, "total": len(self.late_vouchers)}
		if self.late_vouchers:
			summary["html"] = self.render_html(self.LIST_LIMIT)
		return summary

	def render_html(self, limit=None):
		"""
		The voucher table, shared by the submit dialog and the timeline entry
		"""
		vouchers = list(self.late_vouchers.values())
		return frappe.render_template(self.TEMPLATE, {"vouchers": vouchers[:limit], "total": len(vouchers)})

	def record(self):
		"""
		Leave a permanent timeline entry naming every late voucher
		"""
		if not (self.can_run and self.late_vouchers):
			return

		message = _("submitted with {0} transactions posted into its period after the return was created:")
		self.vat_return.add_comment("Info", message.format(len(self.late_vouchers)) + self.render_html())
