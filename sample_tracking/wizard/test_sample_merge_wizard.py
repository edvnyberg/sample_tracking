from collections import Counter

from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.tools.translate import _


# Popup form to pull individual unit serials from sibling split lines back onto this one.
# Any source line left with zero units afterwards is removed; partially-picked
# source lines keep their remaining units and quantity.
class TestSampleMergeWizard(models.TransientModel):
    _name = "test.sample.merge.wizard"
    _description = "Merge Sample Lines"

    line_id = fields.Many2one("test.sample.line", required=True, ondelete="cascade", help="The item that will receive the selected units.")
    sample_id = fields.Many2one("test.sample", readonly=True, help="The batch this item belongs to.")
    line_state = fields.Char(readonly=True, help="Current state of the item. Sibling units can only be pulled from lines in the same state.")
    split_group = fields.Char(readonly=True)
    product_name = fields.Char(string="Product / Item", readonly=True, help="Name of the item, shown for reference.")
    current_quantity = fields.Float(string="Current Quantity", readonly=True, help="Quantity on this line before the merge.")
    selected_unit_ids = fields.Many2many(
        "test.sample.line.unit",
        "test_sample_merge_wizard_unit_rel",
        "wizard_id",
        "unit_id",
        string="Units to Merge In",
        help="Select which physical units, from any sibling line, should be moved onto this line.",
    )
    resulting_quantity = fields.Float(string="Resulting Quantity", compute="_compute_resulting_quantity", help="Total quantity on this line after the merge.")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        line_id = res.get("line_id") or self._context.get("default_line_id")
        if line_id:
            line = self.env["test.sample.line"].browse(line_id)
            res["sample_id"] = line.sample_id.id
            res["line_state"] = line.state
            res["split_group"] = line.split_group
            res["product_name"] = line.product_name
            res["current_quantity"] = line.quantity
        return res

    @api.depends("current_quantity", "selected_unit_ids")
    def _compute_resulting_quantity(self):
        for rec in self:
            rec.resulting_quantity = rec.current_quantity + len(rec.selected_unit_ids)

    _PENDING_STATES = ("scrap_pending", "pending_return")

    def action_confirm(self):
        self.ensure_one()
        line = self.line_id
        selected_units = self.selected_unit_ids
        if not selected_units:
            raise UserError(_("Please select at least one unit to merge into this line."))

        source_lines = selected_units.mapped("line_id")
        if line in source_lines:
            raise UserError(_("Selected units must come from a sibling line, not this line itself."))

        for item, label in ((line, _("This item")), *((src, _("'%s'") % src.product_name) for src in source_lines)):
            if item.state in self._PENDING_STATES:
                raise UserError(
                    _("%s is in state '%s'. Resolve the pending action before merging.") % (label, item.state)
                )
            if item.has_pending_transfer:
                raise UserError(
                    _("%s has a pending transfer. Accept or cancel it before merging.") % label
                )
            if item.state != line.state:
                raise UserError(_("%s must be in the same state as this line to merge.") % label)

        # Count how many units come from each source line before reassigning, since
        # selected_units.line_id will all point to the keeper right after the write.
        # Must count on plain ids: mapped("line_id") would dedupe into a recordset
        # and collapse the per-line tally to 1 regardless of how many units matched.
        counts = Counter(unit.line_id.id for unit in selected_units)
        new_qty = line.quantity + len(selected_units)
        selected_units.sudo().write({"line_id": line.id})
        line.with_context(_skip_serial_sync=True).write({"quantity": new_qty, "ordered_quantity": new_qty})

        for src_id, taken in counts.items():
            src = self.env["test.sample.line"].browse(src_id)
            remaining = src.quantity - taken
            if remaining <= 0:
                src.sudo().unlink()
            else:
                src.with_context(_skip_serial_sync=True).write({"quantity": remaining, "ordered_quantity": remaining})

        # If no other lines share the split group, clear it to keep data clean
        remaining_siblings = self.env["test.sample.line"].search([
            ("split_group", "=", line.split_group),
            ("id", "!=", line.id),
        ])
        if not remaining_siblings:
            line.write({"split_group": False})
        return {"type": "ir.actions.act_window_close"}
