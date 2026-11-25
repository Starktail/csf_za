// Copyright (c) 2024, Dirk van der Laarse and contributors
// For license information, please see license.txt

frappe.ui.form.on("Value-added Tax Return", {
  refresh(frm) {
    frm.trigger("set_intro");

    // Add custom button
    if (!frm.doc.__islocal) {
      frm.add_custom_button(__("Get transactions for period"), function () {
        if (frm.is_dirty()) frappe.throw(__("Please save before proceeding."));
        frappe.call({
          method: "get_gl_entries",
          doc: frm.doc,
          freeze: true,
          freeze_message: __("Retrieving GL Entries..."),
          callback: function (r) {
            if (r.message) {
              // Populate table with new results
              frm.clear_table("gl_entries");
              append_gl_entry_rows(frm, r.message);
              frappe.show_alert({
                message: __("GL Entries have been retrieved"),
                indicator: "green",
              });
              frm.save();
            }
          },
        });
      });
    }

    if (!frm.doc.__islocal && frm.doc.docstatus === 0) {
      frm.add_custom_button(__("Check for late transactions"), () =>
        check_late_entries(frm),
      );
    }
  },
  before_submit(frm) {
    // An empty table is left to the server: retrieve first, or submit as a nil return
    if (!frm.doc.gl_entries.length) return;

    return new Promise((resolve) => {
      const settle = settle_once(resolve);
      request_late_entry_summary(frm)
        .then((summary) => gate_submit(frm, summary, settle))
        .catch((error) => {
          console.error(error);
          settle(false);
        });
    });
  },
  set_intro(frm) {
    // Set the intro message on the form to show unclassified transactions
    const unclassified = frm.doc.gl_entries.filter(
      (entry) =>
        (entry.classification || "").length === 0 && entry.is_cancelled === 0,
    );
    if (unclassified.length > 0) {
      frm.set_intro(
        `<b>${unclassified.length}</b> ` + __("unclassified transactions"),
        "orange",
      );
    } else {
      frm.set_intro("");
    }
  },
  date_from(frm) {
    frm.trigger("clear_gl_entries_after_date_change");
  },
  date_to(frm) {
    frm.trigger("clear_gl_entries_after_date_change");
  },
  include_previous_period_transactions(frm) {
    frm.trigger("clear_gl_entries_after_date_change");
  },
  clear_gl_entries_after_date_change(frm) {
    // Clear the Journal Entries table
    if (frm.doc.gl_entries.length !== 0) {
      frappe.confirm(
        __(
          "Changing dates will clear the Transactions table. Do you want to proceed?",
        ),
        () => {
          // action to perform if Yes is selected
          frm.clear_table("gl_entries");
          frm.refresh_field("gl_entries");
        },
        () => {
          // action to perform if No is selected
          frm.reload_doc();
        },
      );
    }
  },
  button_report_output_a(frm) {
    route_to_report(
      frm.doc.name,
      "Output - A Standard rate (excl capital goods)",
    );
  },
  button_report_output_b(frm) {
    route_to_report(
      frm.doc.name,
      "Output - B Standard rate (only capital goods)",
    );
  },
  button_report_output_c(frm) {
    route_to_report(
      frm.doc.name,
      "Output - C Zero Rated (excl goods exported)",
    );
  },
  button_report_output_d(frm) {
    route_to_report(
      frm.doc.name,
      "Output - D Zero Rated (only goods exported)",
    );
  },
  button_report_output_e(frm) {
    route_to_report(frm.doc.name, "Output - E Exempt");
  },
  button_report_input_a(frm) {
    route_to_report(
      frm.doc.name,
      "Input - A Capital goods and/or services supplied to you (local)",
    );
  },
  button_report_input_b(frm) {
    route_to_report(frm.doc.name, "Input - B Capital goods imported");
  },
  button_report_input_c(frm) {
    route_to_report(
      frm.doc.name,
      "Input - C Other goods supplied to you (excl capital goods)",
    );
  },
  button_report_input_d(frm) {
    route_to_report(
      frm.doc.name,
      "Input - D Other goods imported (excl capital goods)",
    );
  },
});

function route_to_report(docname, classification) {
  // Open the 'Value-added Tax Return Linked Transactions' Report
  frappe.route_options = {
    vat_return: docname,
    classification: classification,
  };
  frappe.set_route(
    "query-report",
    "Value-added Tax Return Linked Transactions",
  );
}

function append_gl_entry_rows(frm, entries) {
  entries.forEach((entry) => {
    let row = frm.add_child("gl_entries");
    row.gl_entry = entry.name;
    row.posting_date = entry.posting_date;
    row.voucher_type = entry.voucher_type;
    row.voucher_no = entry.voucher_no;
    row.taxes_and_charges = entry.taxes_and_charges_template;
    row.tax_account_debit = entry.general_ledger_debit;
    row.tax_account_credit = entry.general_ledger_credit;
    row.classification = entry.classification;
    row.classification_debugging = entry.classification_debugging;
    row.tax_amount = entry.tax_amount;
    row.incl_tax_amount = entry.incl_tax_amount;
    row.is_cancelled = entry.is_cancelled;
  });
  frm.refresh_field("gl_entries");
  frm.get_field("gl_entries").tab.set_active();
  frm.trigger("set_intro");
}

function request_late_entry_summary(frm) {
  return new Promise((resolve, reject) =>
    frappe.call({
      method: "get_late_entry_summary",
      doc: frm.doc,
      freeze: true,
      freeze_message: __("Checking for late transactions..."),
      callback: (r) => resolve(r.message),
      error: reject,
    }),
  );
}

function settle_once(resolve) {
  // Only the first exit counts: onhide also fires when Submit anyway hides the dialog
  let settled = false;
  return (proceed) => {
    if (settled) return;
    settled = true;
    frappe.validated = proceed;
    resolve();
  };
}

function gate_submit(frm, summary, settle) {
  if (!summary.checked) alert_check_skipped();
  if (!summary.total) return settle(true);
  new LateEntryDialog(frm, summary, settle).show();
}

function check_late_entries(frm) {
  if (frm.is_dirty()) frappe.throw(__("Please save before proceeding."));
  if (!frm.doc.gl_entries.length) {
    frappe.throw(
      __("Get transactions for period before checking for late ones."),
    );
  }
  request_late_entry_summary(frm).then((summary) => {
    if (!summary.checked) return alert_check_skipped();
    if (!summary.total) {
      return frappe.show_alert({
        message: __("No late transactions found"),
        indicator: "green",
      });
    }
    new LateEntryDialog(frm, summary).show();
  });
}

function alert_check_skipped() {
  frappe.show_alert({
    message: __(
      "Late transaction check skipped: VAT Return Settings are missing or have no tax accounts",
    ),
    indicator: "orange",
  });
}

class LateEntryDialog {
  // Gating when opened from submit (settle given), informational from the Check button
  constructor(frm, summary, settle) {
    this.frm = frm;
    this.settle = settle || (() => {});
    this.dialog = new frappe.ui.Dialog({
      title: __("Transactions posted after this return was created"),
      size: "large",
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "late_entries",
          options: this.message(summary),
        },
      ],
      primary_action_label: __("Append to return"),
      primary_action: () => this.append(),
      onhide: () => this.settle(false),
    });
    if (settle) {
      this.dialog.set_secondary_action_label(__("Submit anyway"));
      this.dialog.set_secondary_action(() => this.submit_anyway());
    }
  }

  message(summary) {
    const intro = __(
      "{0} transactions were posted into this period after the return was created and are not on it.",
      [summary.total],
    );
    return `<p>${intro}</p>${summary.html}`;
  }

  show() {
    this.dialog.show();
  }

  submit_anyway() {
    this.settle(true);
    this.dialog.hide();
  }

  append() {
    this.settle(false);
    this.dialog.hide();
    // Past a macrotask, so frm.save() cannot flip frappe.validated before savesubmit reads it
    setTimeout(() => append_late_entries(this.frm), 0);
  }
}

function append_late_entries(frm) {
  frappe.call({
    method: "get_late_gl_entries",
    doc: frm.doc,
    freeze: true,
    freeze_message: __("Retrieving late transactions..."),
    callback: (r) => {
      append_gl_entry_rows(frm, r.message || []);
      frm.save();
    },
  });
}
