import subprocess

from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.tools.translate import _


# Popup form to receive all incoming items in a batch at once.
# Pre-fills quantities from the sale order and validates discrepancy notes before saving.
# After confirming, transitions to a print step showing unit serial numbers for label printing.
class TestSampleBulkReceiveWizard(models.TransientModel):
    _name = "test.sample.bulk.receive.wizard"
    _description = "Receive All Items"

    sample_id = fields.Many2one("test.sample", string="Sample", required=True, readonly=True)
    state = fields.Selection(
        [("receive", "Receive"), ("print", "Print Labels")],
        default="receive",
    )
    line_ids = fields.One2many(
        "test.sample.bulk.receive.wizard.line",
        "wizard_id",
        string="Items to Receive",
    )
    print_unit_ids = fields.Many2many(
        "test.sample.line.unit",
        "test_sample_receive_wiz_unit_rel",
        "wizard_id",
        "unit_id",
        string="Units to Print",
    )

    @api.model
    def default_get(self, fields_list):
        # Pre-fill the wizard table with all items still in Incoming state
        res = super().default_get(fields_list)
        sample_id = self._context.get("default_sample_id")
        if sample_id:
            res["sample_id"] = sample_id
            sample = self.env["test.sample"].browse(sample_id)
            incoming = sample.line_ids.filtered(lambda l: l.state == "incoming")
            res["line_ids"] = [
                (0, 0, {
                    "line_id": l.id,
                    "product_name": l.product_name,
                    "ordered_quantity": l.ordered_quantity,
                    "quantity": l.quantity,
                    "discrepancy_note": l.discrepancy_note or False,
                    "inspection_notes": l.inspection_notes or False,
                })
                for l in incoming
            ]
        return res

    def action_confirm(self):
        # First pass: collect all validation errors so the user sees them all at once.
        # Second pass: write the confirmed data only if everything is valid.
        # Then transition to the print step showing unit serials for label printing.
        self.ensure_one()
        today = fields.Date.today()

        # Validate all lines before writing any
        errors = []
        for wline in self.line_ids:
            ordered = wline.ordered_quantity or 0
            received = wline.quantity or 0
            if ordered and received != ordered and not wline.discrepancy_note:
                errors.append(
                    _("%(item)s: received %(r)g but %(o)g were ordered. Please add a discrepancy note.") % {
                        "item": wline.product_name,
                        "r": received,
                        "o": ordered,
                    }
                )
        if errors:
            raise UserError("\n".join(errors))

        processed_line_ids = []
        for wline in self.line_ids:
            line = wline.line_id
            if line.state != "incoming":
                continue
            ordered = wline.ordered_quantity or 0
            received = wline.quantity or 0
            discrepancy_type = self.env["test.sample.line"]._discrepancy_type(ordered, received)

            inspection_result = "damaged" if wline.inspection_notes else "good"

            # sudo: receiving person may not be the assigned responsible for every line
            line.sudo().write({
                "state": "received",
                "quantity": wline.quantity,
                "discrepancy_type": discrepancy_type,
                "discrepancy_note": wline.discrepancy_note,
                "inspection_result": inspection_result,
                "inspection_notes": wline.inspection_notes,
                "date_received": today,
                "date_inspected": today,
            })
            processed_line_ids.append(line.id)

        sample = self.sample_id
        if sample and not sample.date_received:
            sample.date_received = today

        # Collect all unit serials from received lines for the print step
        units = self.env["test.sample.line.unit"].search([("line_id", "in", processed_line_ids)])
        self.write({
            "state": "print",
            "print_unit_ids": [(6, 0, units.ids)],
        })

        return {
            "type": "ir.actions.act_window",
            "res_model": "test.sample.bulk.receive.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "context": self._context,
        }

    def action_print_labels(self):
        self.ensure_one()
        is_logistics = self.env.user.has_group("sample_tracking.group_sample_logistics")
        is_manager = self.env.user.has_group("sample_tracking.group_sample_manager")
        if not is_logistics and not is_manager:
            raise UserError(_("Only the logistics team and managers can print serial number labels."))
        if not self.print_unit_ids:
            raise UserError(_("No unit serials found for the received items. Nothing to print."))
        line_ids = self.print_unit_ids.mapped("line_id").ids
        printer_name = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("sample_tracking.label_printer_name", "")
            .strip()
        )
        if printer_name:
            report = self.env.ref("sample_tracking.action_report_serial_labels")
            pdf_bytes, _ext = self.env["ir.actions.report"]._render_qweb_pdf(
                report.report_name, res_ids=line_ids
            )
            subprocess.run(["lp", "-d", printer_name], input=pdf_bytes, check=True)
            return {"type": "ir.actions.act_window_close"}
        # Browser tab fallback (testing mode); "close" also closes this dialog once the tab opens.
        return {
            "type": "ir.actions.act_url",
            "url": "/report/pdf/sample_tracking.action_report_serial_labels/%s" % ",".join(str(i) for i in line_ids),
            "target": "new",
            "close": True,
        }

    def action_done(self):
        return {"type": "ir.actions.act_window_close"}


class TestSampleBulkReceiveWizardLine(models.TransientModel):
    _name = "test.sample.bulk.receive.wizard.line"
    _description = "Bulk Receive Item Line"

    wizard_id = fields.Many2one(
        "test.sample.bulk.receive.wizard",
        required=True,
        ondelete="cascade",
    )
    line_id = fields.Many2one(
        "test.sample.line",
        string="Sample Line",
        required=True,
        ondelete="cascade",
    )
    product_name = fields.Char(string="Product / Item", readonly=True)
    ordered_quantity = fields.Float(string="Ordered", readonly=True)
    quantity = fields.Float(string="Received Qty")
    discrepancy_note = fields.Text(
        string="Discrepancy Notes",
        help="Required if received quantity differs from ordered quantity.",
    )
    inspection_notes = fields.Text(
        string="Damage Notes",
        help="Fill in if the item is physically damaged. Leave blank if no damage.",
    )
