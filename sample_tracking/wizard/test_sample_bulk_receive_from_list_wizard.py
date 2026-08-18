from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.tools.translate import _


# Bulk receive wizard opened from the item list view (via active_ids).
# Mirrors the sample-form bulk receive but works across items from any sample.
class TestSampleBulkReceiveFromListWizard(models.TransientModel):
    _name = "test.sample.bulk.receive.from.list.wizard"
    _description = "Bulk Receive & Inspect Items"

    line_ids = fields.One2many(
        "test.sample.bulk.receive.from.list.wizard.line",
        "wizard_id",
        string="Items to Receive",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_model = self._context.get("active_model")
        active_ids = self._context.get("active_ids", [])
        if active_model == "test.sample.line" and active_ids:
            eligible = self.env["test.sample.line"].browse(active_ids).filtered(
                lambda l: l.state == "incoming"
            )
            res["line_ids"] = [
                (0, 0, {
                    "line_id": l.id,
                    "product_name": l.product_name,
                    "ordered_quantity": l.ordered_quantity,
                    "quantity": l.quantity or l.ordered_quantity,
                    "discrepancy_note": l.discrepancy_note or False,
                    "inspection_notes": l.inspection_notes or False,
                    "is_damaged": bool(l.inspection_notes),
                })
                for l in eligible
            ]
        return res

    def action_confirm(self):
        self.ensure_one()
        today = fields.Date.today()

        errors = []
        for wline in self.line_ids:
            ordered = wline.ordered_quantity or 0
            received = wline.quantity or 0
            discrepancy_type = self.env["test.sample.line"]._discrepancy_type(ordered, received)
            if discrepancy_type != "none" and not wline.discrepancy_note:
                errors.append(
                    _("%(item)s: received %(r)g but %(o)g were ordered. Please add a discrepancy note.") % {
                        "item": wline.product_name, "r": received, "o": ordered,
                    }
                )
            if wline.is_damaged and not wline.inspection_notes:
                errors.append(
                    _("%(item)s: marked as damaged but no damage notes provided.") % {"item": wline.product_name}
                )
        if errors:
            raise UserError("\n".join(errors))

        for wline in self.line_ids:
            line = wline.line_id
            if line.state != "incoming":
                continue
            ordered = wline.ordered_quantity or 0
            received = wline.quantity or 0
            discrepancy_type = self.env["test.sample.line"]._discrepancy_type(ordered, received)
            line.sudo().write({
                "state": "received",
                "quantity": received,
                "discrepancy_type": discrepancy_type,
                "discrepancy_note": wline.discrepancy_note or False,
                "inspection_result": "damaged" if wline.is_damaged else "good",
                "inspection_notes": wline.inspection_notes or False,
                "date_received": today,
                "date_inspected": today,
            })
            if not line.sample_id.date_received:
                line.sample_id.date_received = today

        return {"type": "ir.actions.act_window_close"}


class TestSampleBulkReceiveFromListWizardLine(models.TransientModel):
    _name = "test.sample.bulk.receive.from.list.wizard.line"
    _description = "Bulk Receive Item Line (List)"

    wizard_id = fields.Many2one(
        "test.sample.bulk.receive.from.list.wizard",
        required=True,
        ondelete="cascade",
    )
    line_id = fields.Many2one("test.sample.line", required=True, ondelete="cascade")
    product_name = fields.Char(string="Product / Item", readonly=True)
    ordered_quantity = fields.Float(string="Ordered", readonly=True)
    quantity = fields.Float(string="Received")
    is_damaged = fields.Boolean(string="Damaged")
    discrepancy_note = fields.Text(string="Discrepancy Notes")
    inspection_notes = fields.Text(string="Damage Notes")
