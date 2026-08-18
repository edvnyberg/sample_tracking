import subprocess

from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.tools.translate import _


# Popup form for logistics to receive and inspect a single incoming item.
# Lets them adjust the received quantity, note any discrepancy, and flag damage.
# Step 1 (state=receive): fill in the form.
# Step 2 (state=confirm): review the summary and label-printing info before committing.
class TestSampleReceiveWizard(models.TransientModel):
    _name = "test.sample.receive.wizard"
    _description = "Receive & Inspect Item"

    line_id = fields.Many2one("test.sample.line", required=True, ondelete="cascade")
    product_name = fields.Char(string="Product / Item", readonly=True)
    ordered_quantity = fields.Float(string="Ordered Quantity", readonly=True)
    quantity = fields.Float(string="Received Quantity")
    is_damaged = fields.Boolean(string="Item is Damaged")
    discrepancy_note = fields.Text(
        string="Discrepancy Notes",
        help="Required if the received quantity differs from the ordered quantity.",
    )
    inspection_notes = fields.Text(
        string="Damage Notes",
        help="Describe the damage. Required when Item is Damaged is checked.",
    )
    state = fields.Selection(
        [("receive", "Receive"), ("confirm", "Confirm")],
        default="receive",
    )
    label_count = fields.Integer(
        string="Labels to Print",
        compute="_compute_label_count",
        help="Number of serial labels that will open after confirming, one per unit.",
    )

    @api.depends("quantity")
    def _compute_label_count(self):
        for rec in self:
            rec.label_count = int(rec.quantity) if rec.quantity and rec.quantity > 0 else 0

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        line_id = res.get("line_id") or self._context.get("default_line_id")
        if line_id:
            line = self.env["test.sample.line"].browse(line_id)
            res["product_name"] = line.product_name
            res["ordered_quantity"] = line.ordered_quantity
            res["quantity"] = line.quantity or line.ordered_quantity
            if line.discrepancy_note:
                res["discrepancy_note"] = line.discrepancy_note
            if line.inspection_notes:
                res["inspection_notes"] = line.inspection_notes
                res["is_damaged"] = True
        return res

    def action_preview(self):
        """Validate input and advance to the confirmation step."""
        self.ensure_one()
        line = self.line_id
        if line.state != "incoming":
            raise UserError(_("This item is no longer Incoming and cannot be received again."))
        ordered = self.ordered_quantity or 0
        received = self.quantity or 0
        discrepancy_type = self.env["test.sample.line"]._discrepancy_type(ordered, received)
        if discrepancy_type != "none" and not self.discrepancy_note:
            raise UserError(_(
                "Received %(r)g but %(o)g were ordered. Please add a discrepancy note.",
            ) % {"r": received, "o": ordered})
        if self.is_damaged and not self.inspection_notes:
            raise UserError(_("Please describe the damage in the Damage Notes field."))
        self.state = "confirm"
        return {
            "type": "ir.actions.act_window",
            "res_model": "test.sample.receive.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_back(self):
        """Return to the receive form so the user can make corrections."""
        self.state = "receive"
        return {
            "type": "ir.actions.act_window",
            "res_model": "test.sample.receive.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_confirm(self):
        self.ensure_one()
        line = self.line_id
        if line.state != "incoming":
            raise UserError(_("This item is no longer Incoming and cannot be received again."))
        ordered = self.ordered_quantity or 0
        received = self.quantity or 0
        discrepancy_type = self.env["test.sample.line"]._discrepancy_type(ordered, received)
        if discrepancy_type != "none" and not self.discrepancy_note:
            raise UserError(_(
                "Received %(r)g but %(o)g were ordered. Please add a discrepancy note.",
            ) % {"r": received, "o": ordered})
        if self.is_damaged and not self.inspection_notes:
            raise UserError(_("Please describe the damage in the Damage Notes field."))
        today = fields.Date.today()
        line.sudo().write({
            "state": "received",
            "quantity": received,
            "discrepancy_type": discrepancy_type,
            "discrepancy_note": self.discrepancy_note or False,
            "inspection_result": "damaged" if self.is_damaged else "good",
            "inspection_notes": self.inspection_notes or False,
            "date_received": today,
            "date_inspected": today,
        })
        if not line.sample_id.date_received:
            line.sample_id.date_received = today
        if line.unit_ids:
            return self._labels_action(line)
        return {"type": "ir.actions.act_window_close"}

    def _labels_action(self, line):
        """Print serial labels and close the dialog.

        CURRENTLY:  opens the PDF in a new browser tab (safe for testing
                    without a physical printer; the user prints from there).

        TO SWITCH TO A LABEL PRINTER:
          1. In Odoo ▸ Settings ▸ Technical ▸ System Parameters, create (or
             edit) the key  ``sample_tracking.label_printer_name``  and set
             its value to the CUPS printer name configured on the server,
             e.g. ``ZEBRA_ZT410``.
          2. Make sure the Odoo Docker container can reach that printer via CUPS
             (``lpstat -p`` should list it).
          The method will then render the PDF silently, pipe it straight to
          ``lp -d <printer>``, and close the dialog with no browser tab.
        """
        report = self.env.ref("sample_tracking.action_report_serial_labels")
        printer_name = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("sample_tracking.label_printer_name", "")
            .strip()
        )
        if printer_name:
            pdf_bytes, _ext = self.env["ir.actions.report"]._render_qweb_pdf(
                report.report_name, res_ids=line.ids
            )
            subprocess.run(["lp", "-d", printer_name], input=pdf_bytes, check=True)
            return {"type": "ir.actions.act_window_close"}
        # Browser tab fallback (testing mode); "close" also closes this dialog once the tab opens.
        return {
            "type": "ir.actions.act_url",
            "url": "/report/pdf/sample_tracking.action_report_serial_labels/%s" % ",".join(str(i) for i in line.ids),
            "target": "new",
            "close": True,
        }
