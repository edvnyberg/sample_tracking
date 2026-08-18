from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.tools.translate import _


_PENDING_STATES = ("scrap_pending", "pending_return")


# Bulk merge wizard: merges groups of split lines back into single lines.
# Selected lines are grouped by their split_group UUID. For each group the user
# picks which line to keep; all other lines in the group are absorbed into it.
class TestSampleBulkMergeWizard(models.TransientModel):
    _name = "test.sample.bulk.merge.wizard"
    _description = "Bulk Merge Split Lines"

    group_ids = fields.One2many(
        "test.sample.bulk.merge.wizard.group",
        "wizard_id",
        string="Merge Groups",
    )
    skipped_info = fields.Char(string="Skipped Lines", readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_ids = self._context.get("active_ids", [])
        if not active_ids:
            return res

        lines = self.env["test.sample.line"].browse(active_ids)

        # Group by split_group; lines without one cannot be bulk-merged
        groups = {}
        skipped = []
        for line in lines:
            if not line.split_group:
                skipped.append(line.product_name)
                continue
            groups.setdefault(line.split_group, []).append(line)

        # Groups with only one selected line cannot merge
        valid_groups = {sg: ls for sg, ls in groups.items() if len(ls) >= 2}
        for sg, ls in groups.items():
            if len(ls) < 2:
                skipped.extend(l.product_name for l in ls)

        group_vals = []
        for sg, ls in valid_groups.items():
            keeper = sorted(ls, key=lambda l: (l.sequence, l.id))[0]
            group_vals.append((0, 0, {
                "split_group": sg,
                "product_name": keeper.product_name,
                "keeper_line_id": keeper.id,
                "line_ids": [(6, 0, [l.id for l in ls])],
                "resulting_quantity": sum(l.quantity for l in ls),
            }))

        res["group_ids"] = group_vals
        if skipped:
            res["skipped_info"] = _(
                "Skipped (not part of a split group or only one line selected per group): %s"
            ) % ", ".join(skipped)
        return res

    def action_confirm(self):
        self.ensure_one()
        if not self.group_ids:
            raise UserError(_("No eligible merge groups found in the selection."))

        # Recover the original selection so we don't touch unrelated lines that
        # happen to share the same split_group (e.g. lines in scrap_pending).
        active_ids = self._context.get("active_ids", [])

        for group in self.group_ids:
            keeper = group.keeper_line_id
            if not keeper:
                raise UserError(_("Please select a line to keep for each merge group."))

            # Re-fetch lines from the DB, scoped to the original selection so
            # we never accidentally touch lines outside what the user picked.
            all_lines = self.env["test.sample.line"].search([
                ("split_group", "=", group.split_group),
                ("id", "in", active_ids),
            ])
            if not all_lines:
                continue
            lines_to_absorb = all_lines.filtered(lambda l: l.id != keeper.id)

            for item in all_lines:
                if item.state in _PENDING_STATES:
                    raise UserError(
                        _("'%s' is in state '%s'. Resolve it before merging.") % (item.product_name, item.state)
                    )
                if item.has_pending_transfer:
                    raise UserError(
                        _("'%s' has a pending transfer. Accept or cancel it before merging.") % item.product_name
                    )

            new_qty = sum(all_lines.mapped("quantity"))
            # Move all serials from absorbed lines to the keeper before deletion
            lines_to_absorb.unit_ids.sudo().write({"line_id": keeper.id})
            keeper.with_context(_skip_serial_sync=True).write({"quantity": new_qty, "ordered_quantity": new_qty})
            lines_to_absorb.sudo().unlink()

            # Clear split_group on the keeper if no other siblings remain
            remaining = self.env["test.sample.line"].search([
                ("split_group", "=", keeper.split_group),
                ("id", "!=", keeper.id),
            ])
            if not remaining:
                keeper.write({"split_group": False})

        return {"type": "ir.actions.act_window_close"}


class TestSampleBulkMergeWizardGroup(models.TransientModel):
    _name = "test.sample.bulk.merge.wizard.group"
    _description = "Bulk Merge Group"

    wizard_id = fields.Many2one("test.sample.bulk.merge.wizard", required=True, ondelete="cascade")
    split_group = fields.Char(readonly=True)
    product_name = fields.Char(string="Product / Item", readonly=True)
    line_ids = fields.Many2many(
        "test.sample.line",
        "bulk_merge_group_line_rel",
        "group_id",
        "line_id",
        string="Lines to Merge",
        readonly=True,
    )
    keeper_line_id = fields.Many2one(
        "test.sample.line",
        string="Keep This Line",
        help="All other lines in this group will be absorbed into this one.",
    )
    resulting_quantity = fields.Float(string="Resulting Quantity", readonly=True)
