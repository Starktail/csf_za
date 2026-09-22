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
              let response = r.message;
              if (response) {
                // Populate table with new results
                frm.clear_table("gl_entries");
                response.forEach((entry) => {
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
                frappe.show_alert({
                  message: __("GL Entries have been retrieved"),
                  indicator: "green",
                });
                frm.save();
              }
            }
          },
        });
      });
    }
  },
  set_intro(frm) {
    // Set the intro message on the form to show unclassified transactions
    const unclassified = frm.doc.gl_entries.filter(
      (entry) => entry.classification.length === 0 && entry.is_cancelled === 0,
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
