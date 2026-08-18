from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.tools.translate import _
from markupsafe import Markup, escape
import uuid


# Each individual physical item within a sample batch. One line per product.
# Moves through a workflow from Incoming through Received, In Testing, and Tested, then ends as Returned or Scrapped.
class TestSampleLine(models.Model):
    _name = "test.sample.line"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Test Sample Line"
    _order = "sequence, id"
    _rec_name = "name"

    # Identity and source fields
    name = fields.Char(
        string="Item Ref",
        copy=False,
        readonly=True,
        default=lambda self: _("New"),
        help="Unique reference number assigned automatically when the item is created.",
    )
    sample_id = fields.Many2one(
        "test.sample",
        string="Sample",
        required=True,
        ondelete="cascade",
        index=True,
        help="The batch this item belongs to.",
    )
    # Product and quantity fields
    sequence = fields.Integer(default=10)
    product_name = fields.Char(string="Product / Item", required=True, help="Name or description of the physical item.")
    quantity = fields.Float(string="Quantity", default=1.0, aggregator=None, help="Number of units physically received and tracked on this line.")
    ordered_quantity = fields.Float(string="Ordered Quantity", default=0.0, help="Number of units the customer originally ordered. Used to detect discrepancies on arrival.")
    discrepancy_type = fields.Selection(
        [
            ("none", "None"),
            ("quantity_short", "Quantity Short"),
            ("quantity_excess", "Quantity Excess"),
            ("missing", "Item Missing"),
            ("wrong_item", "Wrong Item"),
        ],
        string="Discrepancy",
        default="none",
        help="Type of discrepancy found when comparing received quantity against the order.",
    )
    discrepancy_note = fields.Text(string="Discrepancy Notes", help="Explain the discrepancy in detail. Required when the received quantity does not match the ordered quantity.")
    description = fields.Text(string="Description", help="Additional free-text description of the item, such as model number or batch code.")
    sale_order_line_id = fields.Many2one("sale.order.line", string="Sale Order Line", ondelete="set null", help="The specific sale order line this item was received against.")
    customer_id = fields.Many2one(
        "res.partner",
        related="sample_id.customer_id",
        string="Customer",
        store=False,
    )
    sale_order_id = fields.Many2one(
        "sale.order",
        related="sample_id.sale_order_id",
        string="Sales Order",
        store=True,
    )
    task_id = fields.Many2one(
        "project.task",
        string="Task",
        ondelete="set null",
        tracking=True,
        help="Optional task linked to this item during transfer. Set in the transfer wizard.",
    )
    # Ownership and inspection fields
    responsible_id = fields.Many2one(
        "res.users",
        string="Responsible",
        tracking=True,
        help="The user currently responsible for this item. Only the responsible user (or a manager) can move it through the workflow.",
    )
    inspection_result = fields.Selection(
        [
            ("pending", "Pending"),
            ("good", "Good"),
            ("damaged", "Damaged"),
        ],
        string="Inspection",
        default="pending",
        tracking=True,
        help="Result of the physical inspection on arrival. Items must be marked Good before they can be transferred.",
    )
    inspection_notes = fields.Text(string="Inspection Notes", tracking=True, help="Notes recorded during inspection. Required when the item is flagged as Damaged.")
    current_location_id = fields.Many2one(
        "test.sample.location",
        string="Current Location",
        tracking=True,
        help="Where this item is physically stored right now.",
    )
    # Lifecycle state
    state = fields.Selection(
        [
            ("incoming", "Incoming"),
            ("received", "Received"),
            ("in_testing", "In Testing"),
            ("in_transfer", "In Transfer"),
            ("tested", "Tested"),
            ("scrap_pending", "Scrap Pending"),
            ("pending_return", "Pending Return"),
            ("returned", "Returned"),
            ("scrapped", "Scrapped"),
        ],
        string="Status",
        default="incoming",
        required=True,
        tracking=True,
        help=(
            "Lifecycle stage of this item.\n"
            "Incoming: awaiting receipt.\n"
            "Received: received and ready for inspection.\n"
            "In Testing: being tested by the responsible user.\n"
            "In Transfer: a transfer to another user is pending acceptance.\n"
            "Tested: testing is complete.\n"
            "Scrap Pending: a scrap request has been submitted and is awaiting manager approval.\n"
            "Pending Return: return submitted and ready for logistics to dispatch.\n"
            "Returned: sent back to the customer.\n"
            "Scrapped: permanently disposed of."
        ),
    )
    # Saves the state before a transfer so it can be restored if the transfer is declined or cancelled
    pre_transfer_state = fields.Char(string="Pre-Transfer State", copy=False, help="State the item was in before the current transfer began. Restored automatically if the transfer is declined or cancelled.")
    # Key date fields
    date_received = fields.Date(string="Date Received", tracking=True, help="Date the item arrived at the facility.")
    date_inspected = fields.Date(string="Date Inspected", tracking=True, help="Date the inspection was completed.")
    date_closed = fields.Date(string="Date Returned / Scrapped", tracking=True, help="Date the item was returned to the customer or scrapped.")
    return_method = fields.Selection(
        [("pickup", "Customer Pickup"), ("ship", "Ship to Customer")],
        string="Return Method",
        tracking=True,
        help="How the item will be sent back to the customer. Pickup means the customer collects it in person. Ship means it will be dispatched via a courier.",
    )
    return_courier_id = fields.Many2one("test.sample.courier", string="Return Courier", copy=False, tracking=True, help="Courier used to ship the item back to the customer. Set during the return process.")
    customer_courier_account = fields.Char(string="Customer Number at Courier", copy=False, tracking=True, help="Customer's own account number with the courier, if they have one. Used for shipped returns.")
    customer_signature = fields.Binary(string="Customer Signature", copy=False, help="Signature captured when the customer collects the item in person.")
    customer_signature_name = fields.Char(default="signature.png")
    tracking_number = fields.Char(string="Tracking Number", copy=False, tracking=True, help="Courier tracking number recorded by logistics when the shipment is confirmed.")
    # Split and merge tracking fields
    # Lines from the same original split share a UUID here so they can be merged back later
    split_group = fields.Char(index=True, copy=False, default=lambda self: str(uuid.uuid4()), help="UUID shared between lines that were split from the same original. Used to find siblings for merging.")
    is_split = fields.Boolean(compute="_compute_is_split", string="Has Split Siblings", help="True if another line in this batch was split from the same original item.")
    closure_notes = fields.Text(string="Closure Notes", tracking=True, help="Free-text notes recorded when the item is closed, for example reasons for scrapping or special return instructions.")
    # Per-unit serial numbers, one record per physical unit in this line
    unit_ids = fields.One2many(
        "test.sample.line.unit",
        "line_id",
        string="Unit Serials",
        help="Individual serial numbers for each physical unit in this line.",
    )
    # History and approval records
    transfer_ids = fields.One2many(
        "test.sample.transfer",
        "line_id",
        string="Transfer History",
        help="All transfer records for this item, including accepted, declined, and cancelled transfers.",
    )
    scrap_request_ids = fields.One2many(
        "test.sample.scrap.request",
        "line_id",
        string="Scrap Requests",
        help="All scrap requests submitted for this item.",
    )
    # Computed shortcut fields
    pending_scrap_request_id = fields.Many2one(
        "test.sample.scrap.request",
        compute="_compute_pending_scrap_request",
        string="Pending Scrap Request",
    )
    pending_incoming_transfer_id = fields.Many2one(
        "test.sample.transfer",
        compute="_compute_pending_incoming_transfer",
        string="Pending Incoming Transfer",
    )
    has_pending_transfer = fields.Boolean(
        compute="_compute_has_pending_transfer",
        string="Transfer Pending",
    )
    kanban_state_group = fields.Selection(
        [
            ("transfer_pending", "Transfer Pending"),
            ("incoming", "Incoming"),
            ("received", "Received"),
            ("in_testing", "In Testing"),
            ("in_transfer", "In Transfer"),
            ("tested", "Tested"),
            ("scrap_pending", "Scrap Pending"),
            ("pending_return", "Pending Return"),
            ("returned", "Returned"),
            ("scrapped", "Scrapped"),
        ],
        string="Dashboard Group",
        compute="_compute_kanban_state_group",
        store=True,
    )
    logistics_group = fields.Selection(
        [
            ("a_unclaimed", "Unclaimed Incoming"),
            ("b_incoming", "Incoming"),
            ("c_received", "Received"),
            ("d_unclaimed_return", "Unclaimed Returns"),
            ("e_pending_return", "Pending Return"),
        ],
        string="Logistics Group",
        compute="_compute_logistics_group",
        store=True,
        help="Grouping key for the Logistics Items view. Unclaimed incoming items always sort first.",
    )

    @api.depends("split_group", "sample_id.line_ids.split_group")
    def _compute_is_split(self):
        # True if another line in this batch shares the same split group.
        # Build a counter of split_group occurrences per sample to avoid O(n^2) scanning.
        from collections import Counter
        lines_by_sample = {}
        for line in self:
            sid = line.sample_id.id
            if sid not in lines_by_sample:
                lines_by_sample[sid] = Counter(
                    l.split_group for l in line.sample_id.line_ids if l.split_group
                )
        for line in self:
            sid = line.sample_id.id
            counter = lines_by_sample.get(sid, Counter())
            line.is_split = bool(line.split_group) and counter[line.split_group] > 1

    @api.depends("state", "transfer_ids.state")
    def _compute_kanban_state_group(self):
        # Items with a pending transfer appear in their own dashboard group regardless of their actual state
        for line in self:
            line.kanban_state_group = "transfer_pending" if line.has_pending_transfer else line.state

    @api.depends("state", "responsible_id")
    def _compute_logistics_group(self):
        # Separates unclaimed incoming items so they appear as a distinct top group in the logistics view
        for line in self:
            if line.state == "incoming" and not line.responsible_id:
                line.logistics_group = "a_unclaimed"
            elif line.state == "incoming":
                line.logistics_group = "b_incoming"
            elif line.state == "received":
                line.logistics_group = "c_received"
            elif line.state == "pending_return" and not line.responsible_id:
                line.logistics_group = "d_unclaimed_return"
            elif line.state == "pending_return":
                line.logistics_group = "e_pending_return"
            else:
                line.logistics_group = False

    @api.depends("scrap_request_ids.state")
    def _compute_pending_scrap_request(self):
        for line in self:
            line.pending_scrap_request_id = line.scrap_request_ids.filtered(
                lambda r: r.state == "pending"
            )[:1]

    @api.depends("transfer_ids.state")
    def _compute_has_pending_transfer(self):
        for line in self:
            line.has_pending_transfer = any(t.state == "pending" for t in line.transfer_ids)

    @api.depends("transfer_ids.state", "transfer_ids.to_user_id")
    def _compute_pending_incoming_transfer(self):
        # Points to the transfer that the currently logged-in user needs to accept or decline
        for line in self:
            pending = line.transfer_ids.filtered(
                lambda t: t.to_user_id == self.env.user and t.state == "pending"
            )
            line.pending_incoming_transfer_id = pending[:1]

    @staticmethod
    def _discrepancy_type(ordered, received):
        # Given how many were ordered vs. how many actually arrived, return the discrepancy category
        if not ordered:
            return "none"
        if received == 0:
            return "missing"
        if received < ordered:
            return "quantity_short"
        if received > ordered:
            return "quantity_excess"
        return "none"

    @api.onchange("quantity", "ordered_quantity")
    def _onchange_quantity_discrepancy(self):
        # Auto-fill the discrepancy category as the user types quantities in the form
        ordered = self.ordered_quantity or 0
        if not ordered:
            return
        self.discrepancy_type = self._discrepancy_type(ordered, self.quantity or 0)


    def action_claim_item(self):
        # Assign the current logistics user as responsible for this item.
        # Must be called before receiving or inspecting.
        is_logistics = self.env.user.has_group("sample_tracking.group_sample_logistics")
        is_manager = self.env.user.has_group("sample_tracking.group_sample_manager")
        if not is_logistics and not is_manager:
            raise UserError(_("Only the logistics team and managers can claim items."))
        for rec in self:
            if rec.state != "incoming":
                raise UserError(_("Only incoming items can be claimed."))
            if rec.responsible_id:
                raise UserError(
                    _("%(item)s is already claimed by %(user)s.") % {
                        "item": rec.product_name,
                        "user": rec.responsible_id.name,
                    }
                )
            rec.write({"responsible_id": self.env.user.id})

    def action_claim_return(self):
        # Assign the current logistics user as responsible for handling this return.
        is_logistics = self.env.user.has_group("sample_tracking.group_sample_logistics")
        is_manager = self.env.user.has_group("sample_tracking.group_sample_manager")
        if not is_logistics and not is_manager:
            raise UserError(_("Only the logistics team and managers can claim returns."))
        for rec in self:
            if rec.state != "pending_return":
                raise UserError(_("Only pending returns can be claimed."))
            if rec.responsible_id:
                raise UserError(
                    _("%(item)s is already claimed by %(user)s.") % {
                        "item": rec.product_name,
                        "user": rec.responsible_id.name,
                    }
                )
            rec.sudo().write({"responsible_id": self.env.user.id})

    def action_receive(self):
        # Open the receive & inspect wizard so logistics can adjust qty, note discrepancies and flag damage.
        self.ensure_one()
        if self.state != "incoming":
            raise UserError(_("This item is no longer Incoming and cannot be received again."))
        self._assert_is_responsible(_("receive this item"))
        return {
            "name": _("Receive & Inspect"),
            "type": "ir.actions.act_window",
            "res_model": "test.sample.receive.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_line_id": self.id},
        }

    def action_open_bulk_receive_from_list_wizard(self):
        # Called from the list view header button, opens bulk receive for selected incoming items.
        return {
            "name": _("Bulk Receive & Inspect"),
            "type": "ir.actions.act_window",
            "res_model": "test.sample.bulk.receive.from.list.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "active_model": "test.sample.line",
                "active_ids": self.ids,
            },
        }

    def action_bulk_merge_marked(self):
        """Merge marked lines that share a split_group directly, keeping the lowest-sequence line per group.

        Called from the list view header button. Works entirely off the marked recordset
        (self), so it doesn't depend on context surviving a round trip.
        """
        if not self:
            raise UserError(_("Select at least one line to merge."))
        groups = {}
        skipped = []
        for line in self:
            if not line.split_group:
                skipped.append(line.product_name)
                continue
            groups[line.split_group] = groups.get(line.split_group, self.env["test.sample.line"]) | line

        merged_groups = 0
        for lines in groups.values():
            if len(lines) < 2 or len(set(lines.mapped("state"))) > 1:
                skipped.extend(lines.mapped("product_name"))
                continue
            keeper = lines.sorted(key=lambda l: (l.sequence, l.id))[0]
            self._merge_split_lines(keeper, lines - keeper)
            merged_groups += 1

        message = _("Merged %s group(s) of marked lines.") % merged_groups
        if skipped:
            message += " " + _(
                "Skipped (not part of a shared split group, only one marked per group, or mismatched states): %s"
            ) % ", ".join(skipped)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Bulk Merge"),
                "message": message,
                "type": "success" if merged_groups else "warning",
                "sticky": bool(skipped),
            },
        }

    _PENDING_STATES = ("scrap_pending", "pending_return")

    def _merge_split_lines(self, keeper, lines_to_absorb):
        """Absorb lines_to_absorb's units/quantity into keeper, delete them, and clear split_group if it's now alone."""
        for item in (keeper | lines_to_absorb):
            if item.state in self._PENDING_STATES:
                raise UserError(
                    _("'%s' is in state '%s'. Resolve the pending action before merging.") % (item.product_name, item.state)
                )
            if item.has_pending_transfer:
                raise UserError(
                    _("'%s' has a pending transfer. Accept or cancel it before merging.") % item.product_name
                )
        new_qty = keeper.quantity + sum(lines_to_absorb.mapped("quantity"))
        lines_to_absorb.unit_ids.sudo().write({"line_id": keeper.id})
        keeper.with_context(_skip_serial_sync=True).write({"quantity": new_qty, "ordered_quantity": new_qty})
        lines_to_absorb.sudo().unlink()
        remaining = self.env["test.sample.line"].search([
            ("split_group", "=", keeper.split_group),
            ("id", "!=", keeper.id),
        ])
        if not remaining:
            keeper.write({"split_group": False})

    def action_explode_to_units(self):
        """Split every unit on this line into its own line of quantity=1."""
        self.ensure_one()
        if self.state in self._PENDING_STATES:
            raise UserError(_("Cannot explode an item that is in state '%s'. Resolve the pending action first.") % self.state)
        if self.has_pending_transfer:
            raise UserError(_("Cannot explode an item with a pending transfer. Accept or cancel it first."))
        qty = int(self.quantity)
        if qty <= 1:
            raise UserError(_("Nothing to explode, the quantity is already 1."))
        units = self.env["test.sample.line.unit"].search(
            [("line_id", "=", self.id)], order="serial_number asc"
        )
        # If no serials exist yet, generate them first so each new line gets one.
        if not units:
            self._sync_unit_serials()
            units = self.env["test.sample.line.unit"].search(
                [("line_id", "=", self.id)], order="serial_number asc"
            )
        # After a previous merge, split_group may have been cleared.
        # Assign a fresh UUID so all exploded lines can find each other.
        if not self.split_group:
            self.with_context(_skip_serial_sync=True).write({"split_group": str(uuid.uuid4())})
        def _line_vals(quantity):
            return {
                "sample_id": self.sample_id.id,
                "sequence": self.sequence,
                "product_name": self.product_name,
                "quantity": quantity,
                "ordered_quantity": quantity,
                "description": self.description,
                "current_location_id": self.current_location_id.id if self.current_location_id else False,
                "responsible_id": self.responsible_id.id if self.responsible_id else False,
                "state": self.state,
                "date_received": self.date_received,
                "inspection_result": self.inspection_result,
                "inspection_notes": self.inspection_notes,
                "sale_order_line_id": self.sale_order_line_id.id if self.sale_order_line_id else False,
                "split_group": self.split_group,
            }
        # Set original line to qty=1 (keeps the first serial).
        self.with_context(_skip_serial_sync=True).write({"quantity": 1, "ordered_quantity": 1})
        # Create one new line per remaining serial.
        for unit in units[1:]:
            new_line = self.env["test.sample.line"].with_context(_skip_serial_sync=True).create(_line_vals(1))
            unit.sudo().write({"line_id": new_line.id})
        return {"type": "ir.actions.act_window_close"}

    def action_merge_all_siblings(self):
        """Merge all lines from the same split group back into this line."""
        self.ensure_one()
        if not self.split_group:
            raise UserError(_("This item has no split siblings to merge."))
        siblings = self.env["test.sample.line"].search([
            ("split_group", "=", self.split_group),
            ("id", "!=", self.id),
        ])
        if not siblings:
            raise UserError(_("No sibling lines found for this item. They may have already been merged."))
        self._merge_split_lines(self, siblings)
        return {"type": "ir.actions.act_window_close"}

    def action_pass_inspection(self):
        # Item passed visual inspection: mark it Good and move to In Testing
        self.ensure_one()
        if self.state != "received":
            raise UserError(_("This item must be in Received state to be inspected."))
        self._assert_is_responsible(_("inspect this item"))
        self.write({
            "inspection_result": "good",
            "state": "in_testing",
            "date_inspected": fields.Date.today(),
        })

    def action_open_inspect_wizard(self):
        """Flag as Damaged, inline, no wizard."""
        self.ensure_one()
        if self.state != "received":
            raise UserError(_("This item must be in Received state to be inspected."))
        self._assert_is_responsible(_("inspect this item"))
        if not self.inspection_notes:
            raise UserError(_("Please fill in the Inspection Notes field to describe the damage before flagging."))
        self.write({
            "inspection_result": "damaged",
            "state": "in_testing",
            "date_inspected": fields.Date.today(),
        })

    @api.model_create_multi
    def create(self, vals_list):
        # If no ordered quantity was provided (e.g. manually added line), default it to the received quantity.
        # Assign a unique item reference from the sequence.
        for vals in vals_list:
            if not vals.get("ordered_quantity"):
                vals["ordered_quantity"] = vals.get("quantity", 1.0)
            if not vals.get("name") or vals.get("name") == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("test.sample.line") or _("New")
        records = super().create(vals_list)
        # Split/explode wizards pass this to reassign existing units themselves, without also auto-generating new ones.
        if not self._context.get("_skip_serial_sync"):
            records._sync_unit_serials()
        return records

    def write(self, vals):
        res = super().write(vals)
        if "quantity" in vals and not self._context.get("_skip_serial_sync"):
            self._sync_unit_serials()
        return res

    def _sync_unit_serials(self):
        """Ensure the number of unit serial records matches int(quantity).

        - Creates new serials for any increase in quantity using the format
          ``{line.name}-{n:02d}`` where n continues from the current max.
        - Removes the last N serials (sorted descending) for any decrease.
        - Uses ``_skip_serial_sync`` context flag to allow split/merge wizards
          to manage serials themselves without triggering a secondary sync.
        """
        Unit = self.env["test.sample.line.unit"].sudo()
        for rec in self:
            target = int(rec.quantity)
            existing = Unit.search([("line_id", "=", rec.id)], order="serial_number asc")
            current_count = len(existing)
            if target > current_count:
                Unit.create([
                    {
                        "line_id": rec.id,
                        "serial_number": "{}-{:02d}".format(rec.name, current_count + i + 1),
                    }
                    for i in range(target - current_count)
                ])
            elif target < current_count:
                to_remove = existing[target:]
                to_remove.unlink()

    def _assert_is_responsible(self, action):
        # Only the assigned responsible user (or a manager) may take action on this item
        if self.responsible_id and self.responsible_id != self.env.user:
            if not self.env.user.has_group("sample_tracking.group_sample_manager"):
                raise UserError(
                    _("Only the responsible user (%s) can %s.") % (self.responsible_id.name, action)
                )

    def action_open_bulk_return_wizard(self):
        # Only proceed if at least one selected item is in tested state.
        if not any(line.state == "tested" for line in self):
            raise UserError(_("None of the selected items are in Tested state. Only tested items can be bulk returned."))
        return {
            "name": _("Bulk Return"),
            "type": "ir.actions.act_window",
            "res_model": "test.sample.bulk.return.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {**self.env.context, "active_ids": self.ids, "active_model": "test.sample.line"},
        }

    def action_open_bulk_scrap_wizard(self):
        # Only proceed if at least one selected item is eligible for scrapping.
        if not any(line.state == "tested" and not line.pending_scrap_request_id and not line.has_pending_transfer for line in self):
            raise UserError(_("None of the selected items are eligible for scrapping. Only tested items with no pending scrap request or transfer can be bulk scrapped."))
        return {
            "name": _("Bulk Scrap"),
            "type": "ir.actions.act_window",
            "res_model": "test.sample.bulk.scrap.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {**self.env.context, "active_ids": self.ids, "active_model": "test.sample.line"},
        }

    def action_open_bulk_transfer_wizard(self):
        # Open bulk transfer wizard pre-filled with the selected lines.
        return {
            "name": _("Bulk Transfer"),
            "type": "ir.actions.act_window",
            "res_model": "test.sample.bulk.transfer.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {**self.env.context, "active_ids": self.ids, "active_model": "test.sample.line"},
        }

    def action_open_transfer_wizard(self):
        # Open the transfer form to hand this item off to another user or location
        self.ensure_one()
        self._assert_is_responsible(_("initiate a transfer"))
        if self.state not in ("received", "in_testing"):
            raise UserError(_("Only items in Received or In Testing state can be transferred."))
        if self.state == "in_testing" and self.inspection_result != "good":
            raise UserError(
                _("Item must pass inspection (Good) before it can be transferred from In Testing.")
            )
        return {
            "name": _("Transfer Item"),
            "type": "ir.actions.act_window",
            "res_model": "test.sample.transfer.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_line_id": self.id},
        }

    def action_accept_pending_transfer(self):
        self.ensure_one()
        if self.pending_incoming_transfer_id:
            self.pending_incoming_transfer_id.action_accept_transfer()

    def action_decline_pending_transfer(self):
        self.ensure_one()
        if self.pending_incoming_transfer_id:
            self.pending_incoming_transfer_id.action_decline_transfer()

    def action_mark_tested(self):
        # Mark one or many items as fully tested, works from both form and list views
        for line in self:
            if line.state != "in_testing":
                raise UserError(_("Only items currently in testing can be marked as tested."))
            line._assert_is_responsible(_("mark this item as tested"))
        self.write({"state": "tested"})

    def action_return_to_customer(self):
        self.ensure_one()
        if self.state != "tested":
            raise UserError(_("Only tested items can be returned to the customer."))
        self._assert_is_responsible(_("return this item"))
        return {
            "name": _("Return to Customer"),
            "type": "ir.actions.act_window",
            "res_model": "test.sample.return.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_line_id": self.id,
                "default_product_name": self.product_name,
            },
        }

    def action_scrap(self):
        # Managers can scrap an item immediately; regular users must go through an approval request
        self.ensure_one()
        if self.env.user.has_group("sample_tracking.group_sample_manager"):
            if self.state != "tested":
                raise UserError(_("Only tested items can be scrapped."))
            self.write({"state": "scrapped", "date_closed": fields.Date.today()})
            return {"type": "ir.actions.act_window_close"}
        return {
            "name": _("Request Scrap Approval"),
            "type": "ir.actions.act_window",
            "res_model": "test.sample.scrap.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_line_id": self.id},
        }

    def action_approve_scrap(self):
        self.ensure_one()
        self.pending_scrap_request_id.action_approve()

    def action_reject_scrap(self):
        self.ensure_one()
        self.pending_scrap_request_id.action_reject()

    def action_cancel_scrap_request(self):
        self.ensure_one()
        self.pending_scrap_request_id.action_cancel()

    def action_cancel_transfer(self):
        # Cancel the outstanding transfer, item stays with the current responsible user
        self.ensure_one()
        pending = self.transfer_ids.filtered(lambda t: t.state == "pending")
        if not pending:
            raise UserError(_("No pending transfer found to cancel."))
        pending[0].action_cancel_transfer()

    def action_open_split_wizard(self):
        # Open the wizard to divide this item's quantity into two separate tracking lines
        self.ensure_one()
        pending = self.transfer_ids.filtered(lambda t: t.state == "pending")
        if pending:
            raise UserError(
                _("Cannot split a line with a pending transfer. Accept or cancel the transfer first.")
            )
        return {
            "name": _("Split Line"),
            "type": "ir.actions.act_window",
            "res_model": "test.sample.split.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_line_id": self.id},
        }

    def action_open_merge_wizard(self):
        # Open the wizard to merge this line back with another line from the same original split
        self.ensure_one()
        if self.transfer_ids.filtered(lambda t: t.state == "pending"):
            raise UserError(_(
                "Cannot merge a line with a pending transfer. Accept or cancel the transfer first."
            ))
        candidates = self.env["test.sample.line"].search([
            ("split_group", "=", self.split_group),
            ("state", "=", self.state),
            ("id", "!=", self.id),
        ])
        if not candidates:
            raise UserError(_(
                "This line has not been split, or no lines from the same split share its current status."
            ))
        ctx = {"default_line_id": self.id}
        if len(candidates) == 1:
            ctx["default_selected_unit_ids"] = candidates.unit_ids.ids
        return {
            "name": _("Merge Lines"),
            "type": "ir.actions.act_window",
            "res_model": "test.sample.merge.wizard",
            "view_mode": "form",
            "target": "new",
            "context": ctx,
        }

    def action_confirm_shipment(self):
        # Open the wizard so logistics can enter a tracking number before confirming.
        self.ensure_one()
        if self.state != "pending_return" or self.return_method != "ship":
            raise UserError(_("Only items pending return via shipment can be confirmed here."))
        return {
            "name": _("Confirm Shipment"),
            "type": "ir.actions.act_window",
            "res_model": "test.sample.confirm.shipment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_line_id": self.id},
        }

    def action_confirm_pickup(self):
        # Open the wizard so logistics can optionally capture a customer signature.
        self.ensure_one()
        if self.state != "pending_return" or self.return_method != "pickup":
            raise UserError(_("Only items pending return via customer pickup can be confirmed here."))
        return {
            "name": _("Confirm Customer Pickup"),
            "type": "ir.actions.act_window",
            "res_model": "test.sample.confirm.pickup.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_line_id": self.id},
        }

    def action_cancel_return(self):
        # Cancel a pending return and notify logistics so they stop any dispatch process.
        self.ensure_one()
        if self.state != "pending_return":
            raise UserError(_("Only items in Pending Return state can have their return cancelled."))
        if not self.env.user.has_group("sample_tracking.group_sample_manager"):
            self._assert_is_responsible(_("cancel this return"))
        old_method = self.return_method
        self.write({
            "state": "tested",
            "return_method": False,
            "return_courier_id": False,
        })
        self.message_post(
            body=Markup(
                _("<b>Return cancelled</b> by %(user)s. Item has been moved back to Tested.")
            ) % {"user": self.env.user.name},
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        self._send_return_cancellation_notification(old_method)

    def _send_return_cancellation_notification(self, old_method):
        # Email logistics to let them know the return has been cancelled.
        logistics_email = self.env["ir.config_parameter"].sudo().get_param(
            "sample_tracking.logistics_email"
        )
        if not logistics_email:
            return
        sample = self.sample_id
        method_label = "Ship to Customer" if old_method == "ship" else "Customer Pickup" if old_method == "pickup" else "Unknown"
        rows = [
            ("Item Ref", self.name),
            ("Item", self.product_name),
            ("Sample Ref", sample.name),
            ("Return Method", method_label),
            ("Cancelled By", self.env.user.name),
            ("Date", fields.Date.today().strftime("%d %B %Y")),
        ]
        if self.sale_order_id:
            rows.insert(3, ("Sale Order", self.sale_order_id.name))
        table_rows = Markup("").join(
            Markup(
                "<tr>"
                "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
                "<strong>{label}</strong>"
                "</td>"
                "<td style='padding: 6px 0; color: #222;'>{value}</td>"
                "</tr>"
            ).format(label=escape(label), value=escape(value))
            for label, value in rows
        )
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        item_url = "%s/web#model=test.sample.line&id=%d&view_type=form" % (base_url, self.id)
        body_html = Markup(
            "<div style='font-family: Arial, sans-serif; font-size: 14px; color: #222;'>"
            "<p style='margin-bottom: 16px;'>"
            "A sample return has been <strong>cancelled</strong>. Please stop any dispatch or collection process for this item."
            "</p>"
            "<table style='border-collapse: collapse;'>{rows}</table>"
            "<p style='margin-top: 20px;'>"
            "<a href='{url}' style='background: #875A7B; color: #fff; padding: 8px 16px; "
            "text-decoration: none; border-radius: 4px; font-weight: bold;'>View Item</a>"
            "</p>"
            "</div>"
        ).format(rows=table_rows, url=item_url)
        self.env["mail.mail"].sudo().create({
            "subject": "Return Cancelled - %s / %s" % (self.product_name, sample.name),
            "email_from": self.env.company.email or "",
            "email_to": logistics_email,
            "body_html": body_html,
            "auto_delete": True,
        }).send()

    def action_reopen(self):
        # Manager-only: undo a returned or scrapped closure and put the item back to Tested.
        # Items in pending_return must use action_cancel_return so logistics are notified.
        self.ensure_one()
        if not self.env.user.has_group("sample_tracking.group_sample_manager"):
            raise UserError(_("Only managers can reopen a closed item."))
        if self.state == "pending_return":
            raise UserError(_("Use the Cancel Return action to revert a pending return. This ensures logistics are notified."))
        if self.state not in ("returned", "scrapped"):
            raise UserError(_("Only returned or scrapped items can be reopened."))
        self.write({"state": "tested", "date_closed": False, "return_method": False, "return_courier_id": False})

    def action_revert_to_in_testing(self):
        # Step back from Tested to In Testing
        self.ensure_one()
        if self.state != "tested":
            raise UserError(_("Only tested items can be reverted to In Testing."))
        self._assert_is_responsible(_("revert this item to In Testing"))
        self.write({"state": "in_testing"})

    def action_revert_to_received(self):
        # Manager-only: step back from In Testing to Received, clearing the inspection record
        self.ensure_one()
        if not self.env.user.has_group("sample_tracking.group_sample_manager"):
            raise UserError(_("Only managers can revert item stages."))
        if self.state != "in_testing":
            raise UserError(_("Only items in testing can be reverted to Received."))
        self.write({
            "state": "received",
            "inspection_result": "pending",
            "inspection_notes": False,
            "date_inspected": False,
        })

    def action_revert_to_incoming(self):
        # Logistics and managers can step an item back to Incoming, clearing the received date
        self.ensure_one()
        is_logistics = self.env.user.has_group("sample_tracking.group_sample_logistics")
        is_manager = self.env.user.has_group("sample_tracking.group_sample_manager")
        if not is_logistics and not is_manager:
            raise UserError(_("Only the logistics team and managers can revert items to Incoming."))
        if self.state != "received":
            raise UserError(_("Only received items can be reverted to Incoming."))
        # Use sudo so the write isn't blocked by the responsible-user record rule
        # (logistics may need to revert items claimed by another logistics user)
        self.sudo().write({"state": "incoming", "date_received": False})

    def _send_logistics_notification(self, courier_account=None, moving_info=None):
        # Email the logistics team when a ship return is approved.
        # The recipient address is configurable via Settings > Technical > System Parameters
        # (key: sample_tracking.logistics_email).
        # Optional parameters:
        #   courier_account: customer's account number at the courier (str)
        #   moving_info: dict with keys 'location', 'notes', 'on_pallet' for help with moving
        logistics_email = self.env["ir.config_parameter"].sudo().get_param(
            "sample_tracking.logistics_email"
        )
        if not logistics_email:
            return
        sample = self.sample_id
        customer = sample.customer_id
        courier = self.return_courier_id
        subject = "Sample Return - %s / %s" % (self.product_name, sample.name)
        address_parts = [
            customer.name or "",
            customer.street or "",
            customer.street2 or "",
            " ".join(filter(None, [customer.zip, customer.city])),
            customer.state_id.name if customer.state_id else "",
            customer.country_id.name if customer.country_id else "",
        ]
        address_html = Markup("<br/>").join(escape(p) for p in address_parts if p and p.strip())
        rows = [
            ("Item Ref", self.name),
            ("Item", self.product_name),
            ("Sample Ref", sample.name),
        ]
        if self.sale_order_id:
            rows.append(("Sale Order", self.sale_order_id.name))
        rows.append(
            ("Courier", courier.name if courier else "Not specified")
        )
        if courier_account:
            rows.append(("Customer Account at Courier", courier_account))
        if moving_info:
            rows.append(("Requires Help Moving", "Yes"))
            if moving_info.get("location"):
                rows.append(("Location", moving_info["location"]))
            if moving_info.get("on_pallet"):
                rows.append(("On a Pallet?", moving_info["on_pallet"]))
            if moving_info.get("notes"):
                rows.append(("Moving Notes", moving_info["notes"]))
        table_rows = Markup("").join(
            Markup(
                "<tr>"
                "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
                "<strong>{label}</strong>"
                "</td>"
                "<td style='padding: 6px 0; color: #222;'>{value}</td>"
                "</tr>"
            ).format(label=escape(label), value=escape(value))
            for label, value in rows
        ) + Markup(
            "<tr>"
            "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
            "<strong>Delivery Address</strong>"
            "</td>"
            "<td style='padding: 6px 0; color: #222;'>{address}</td>"
            "</tr>"
            "<tr>"
            "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
            "<strong>Date</strong>"
            "</td>"
            "<td style='padding: 6px 0; color: #222;'>{date}</td>"
            "</tr>"
        ).format(address=address_html, date=escape(fields.Date.today().strftime("%d %B %Y")))
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        item_url = "%s/web#model=test.sample.line&id=%d&view_type=form" % (base_url, self.id)
        body_html = Markup(
            "<div style='font-family: Arial, sans-serif; font-size: 14px; color: #222;'>"
            "<p style='margin-bottom: 16px;'>A sample item return has been approved and is to be shipped to the customer.</p>"
            "<table style='border-collapse: collapse;'>{rows}</table>"
            "<p style='margin-top: 20px;'>"
            "<a href='{url}' style='background: #875A7B; color: #fff; padding: 8px 16px; "
            "text-decoration: none; border-radius: 4px; font-weight: bold;'>View Item</a>"
            "</p>"
            "</div>"
        ).format(rows=table_rows, url=item_url)
        self.env["mail.mail"].sudo().create({
            "subject": subject,
            "email_from": self.env.company.email or "",
            "email_to": logistics_email,
            "body_html": body_html,
            "auto_delete": True,
        }).send()

    def _send_pickup_notification(self, moving_info=None):
        # Email the logistics team when a customer pickup return is approved.
        # The recipient address is configurable via Settings > Technical > System Parameters
        # (key: sample_tracking.logistics_email).
        # Optional parameter:
        #   moving_info: dict with keys 'location', 'notes', 'on_pallet' for help with moving
        logistics_email = self.env["ir.config_parameter"].sudo().get_param(
            "sample_tracking.logistics_email"
        )
        if not logistics_email:
            return
        sample = self.sample_id
        rows = [
            ("Item Ref", self.name),
            ("Item", self.product_name),
            ("Sample Ref", sample.name),
        ]
        if self.sale_order_id:
            rows.append(("Sale Order", self.sale_order_id.name))
        if moving_info:
            rows.append(("Requires Help Moving", "Yes"))
            if moving_info.get("location"):
                rows.append(("Location", moving_info["location"]))
            if moving_info.get("on_pallet"):
                rows.append(("On a Pallet?", moving_info["on_pallet"]))
            if moving_info.get("notes"):
                rows.append(("Moving Notes", moving_info["notes"]))
        rows.append(("Date", fields.Date.today().strftime("%d %B %Y")))
        table_rows = Markup("").join(
            Markup(
                "<tr>"
                "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
                "<strong>{label}</strong>"
                "</td>"
                "<td style='padding: 6px 0; color: #222;'>{value}</td>"
                "</tr>"
            ).format(label=escape(label), value=escape(value))
            for label, value in rows
        )
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        item_url = "%s/web#model=test.sample.line&id=%d&view_type=form" % (base_url, self.id)
        body_html = Markup(
            "<div style='font-family: Arial, sans-serif; font-size: 14px; color: #222;'>"
            "<p style='margin-bottom: 16px;'>A sample item return has been approved. The customer will collect it in person.</p>"
            "<table style='border-collapse: collapse;'>{rows}</table>"
            "<p style='margin-top: 20px;'>"
            "<a href='{url}' style='background: #875A7B; color: #fff; padding: 8px 16px; "
            "text-decoration: none; border-radius: 4px; font-weight: bold;'>View Item</a>"
            "</p>"
            "</div>"
        ).format(rows=table_rows, url=item_url)
        self.env["mail.mail"].sudo().create({
            "subject": "Sample Pickup - %s / %s" % (self.product_name, sample.name),
            "email_from": self.env.company.email or "",
            "email_to": logistics_email,
            "body_html": body_html,
            "auto_delete": True,
        }).send()

    def _send_scrap_moving_notification(self, moving_info):
        # Email the logistics team when a scrap item requires help moving.
        # The recipient address is configurable via Settings > Technical > System Parameters
        # (key: sample_tracking.logistics_email).
        # Parameter:
        #   moving_info: dict with keys 'location', 'notes', 'on_pallet' for help with moving
        logistics_email = self.env["ir.config_parameter"].sudo().get_param(
            "sample_tracking.logistics_email"
        )
        if not logistics_email:
            return
        sample = self.sample_id
        subject = "Sample Scrap - Moving Assistance Needed - %s / %s" % (self.product_name, sample.name)
        rows = [
            ("Item Ref", self.name),
            ("Item", self.product_name),
            ("Sample Ref", sample.name),
        ]
        if self.sale_order_id:
            rows.append(("Sale Order", self.sale_order_id.name))
        rows.append(("Status", "Scrap Pending"))
        if moving_info:
            rows.append(("Requires Help Moving", "Yes"))
            if moving_info.get("location"):
                rows.append(("Location", moving_info["location"]))
            if moving_info.get("on_pallet"):
                rows.append(("On a Pallet?", moving_info["on_pallet"]))
            if moving_info.get("notes"):
                rows.append(("Moving Notes", moving_info["notes"]))
        table_rows = Markup("").join(
            Markup(
                "<tr>"
                "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
                "<strong>{label}</strong>"
                "</td>"
                "<td style='padding: 6px 0; color: #222;'>{value}</td>"
                "</tr>"
            ).format(label=escape(label), value=escape(value))
            for label, value in rows
        )
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        item_url = "%s/web#model=test.sample.line&id=%d&view_type=form" % (base_url, self.id)
        body_html = Markup(
            "<div style='font-family: Arial, sans-serif; font-size: 14px; color: #222;'>"
            "<p style='margin-bottom: 16px;'>A sample item is pending scrap and requires assistance with moving. "
            "Please coordinate with the relevant team.</p>"
            "<table style='border-collapse: collapse;'>{rows}</table>"
            "<p style='margin-top: 20px;'>"
            "<a href='{url}' style='background: #875A7B; color: #fff; padding: 8px 16px; "
            "text-decoration: none; border-radius: 4px; font-weight: bold;'>View Item</a>"
            "</p>"
            "</div>"
        ).format(rows=table_rows, url=item_url)
        self.env["mail.mail"].sudo().create({
            "subject": subject,
            "email_from": self.env.company.email or "",
            "email_to": logistics_email,
            "body_html": body_html,
            "auto_delete": True,
        }).send()
