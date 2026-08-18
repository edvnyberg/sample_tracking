from odoo import models, fields, api
from odoo.exceptions import ValidationError
from odoo.tools.translate import _


# Popup form to divide one item line into separate tracking lines by selecting
# exactly which physical units (serials) to split off.
# All resulting lines inherit the original's state, location, and responsible user,
# and share a split_group UUID so they can be merged back later.
class TestSampleSplitWizard(models.TransientModel):
    _name = "test.sample.split.wizard"
    _description = "Split Sample Line"

    line_id = fields.Many2one("test.sample.line", required=True, ondelete="cascade", help="The item to split.")
    product_name = fields.Char(string="Product / Item", readonly=True, help="Name of the item, shown for reference.")
    original_quantity = fields.Float(string="Current Quantity", readonly=True, help="Total quantity on this line before the split.")

    selected_unit_ids = fields.Many2many(
        "test.sample.line.unit",
        "test_sample_split_wizard_unit_rel",
        "wizard_id",
        "unit_id",
        string="Units to Split Off",
        help="Select exactly which physical units to move to a new line. The rest will remain on the original.",
    )
    split_quantity = fields.Integer(
        string="Splitting Off",
        compute="_compute_quantities",
        help="Number of units that will be split off (derived from your selection).",
    )
    remaining_quantity = fields.Float(
        string="Remaining",
        compute="_compute_quantities",
        help="Quantity that will stay on the original line after the split.",
    )

    @api.depends("selected_unit_ids", "original_quantity")
    def _compute_quantities(self):
        for rec in self:
            rec.split_quantity = len(rec.selected_unit_ids)
            rec.remaining_quantity = rec.original_quantity - len(rec.selected_unit_ids)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        line_id = res.get("line_id") or self._context.get("default_line_id")
        if line_id:
            line = self.env["test.sample.line"].browse(line_id)
            res["product_name"] = line.product_name
            res["original_quantity"] = line.quantity
        return res

    def _line_vals(self, line, quantity):
        return {
            "sample_id": line.sample_id.id,
            "sequence": line.sequence,
            "product_name": line.product_name,
            "quantity": quantity,
            "ordered_quantity": quantity,
            "description": line.description,
            "current_location_id": line.current_location_id.id if line.current_location_id else False,
            "responsible_id": line.responsible_id.id if line.responsible_id else False,
            "state": line.state,
            "date_received": line.date_received,
            "inspection_result": line.inspection_result,
            "inspection_notes": line.inspection_notes,
            "sale_order_line_id": line.sale_order_line_id.id if line.sale_order_line_id else False,
            "split_group": line.split_group,
        }

    _PENDING_STATES = ("scrap_pending", "pending_return")

    def action_confirm(self):
        line = self.line_id
        if line.state in self._PENDING_STATES:
            raise ValidationError(
                _("Cannot split an item that is in state '%s'. Resolve the pending action first.") % line.state
            )
        if line.has_pending_transfer:
            raise ValidationError(_(
                "Cannot split an item with a pending transfer. Accept or cancel it first."
            ))
        if not self.selected_unit_ids:
            raise ValidationError(_("Please select at least one unit to split off."))
        all_units_count = len(line.unit_ids)
        if all_units_count == 0:
            raise ValidationError(_(
                "This item has no unit serials assigned. "
                "Save the item once to generate serials, then try splitting again."
            ))
        if len(self.selected_unit_ids) >= all_units_count:
            raise ValidationError(_(
                "You must keep at least one unit on the original line. "
                "To reassign all units, use the Transfer function instead."
            ))
        split_qty = len(self.selected_unit_ids)
        remaining = line.quantity - split_qty
        # After a previous merge, split_group may have been cleared.
        # Assign a fresh UUID so siblings can find each other again.
        if not line.split_group:
            import uuid as _uuid
            line.with_context(_skip_serial_sync=True).write({"split_group": str(_uuid.uuid4())})
        line.with_context(_skip_serial_sync=True).write(
            {"quantity": remaining, "ordered_quantity": remaining}
        )
        new_line = self.env["test.sample.line"].with_context(_skip_serial_sync=True).create(
            self._line_vals(line, split_qty)
        )
        self.selected_unit_ids.sudo().write({"line_id": new_line.id})
        return {"type": "ir.actions.act_window_close"}

    def action_explode(self):
        """Explode the line into one line per unit directly from the split wizard."""
        return self.line_id.action_explode_to_units()
