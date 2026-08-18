from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.tools.translate import _


# A sample batch that groups all the individual items received from one customer,
# usually linked to a sale order. Each batch gets a unique reference number (SMPL/...).
class TestSample(models.Model):
    _name = "test.sample"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Test Sample"
    _order = "id desc"

    # Identity fields
    name = fields.Char(
        string="Reference",
        required=True,
        copy=False,
        readonly=True,
        default="New",
        help="Unique batch reference assigned automatically on save, for example SMPL/2026/0001.",
    )
    customer_id = fields.Many2one(
        "res.partner",
        string="Customer",
        required=True,
        tracking=True,
        help="The customer who sent in this batch of samples.",
    )
    date_received = fields.Date(string="Date Received", tracking=True, help="Date the batch arrived at the facility.")
    sale_order_id = fields.Many2one("sale.order", string="Sale Order", tracking=True, ondelete="set null", help="Sale order this sample batch is linked to. Used to pull ordered quantities and customer details.")

    # Customer address fields, mirrored from the contact record for quick reference on the form
    customer_street = fields.Char(related="customer_id.street", string="Street", readonly=True)
    customer_street2 = fields.Char(related="customer_id.street2", string="Street 2", readonly=True)
    customer_city = fields.Char(related="customer_id.city", string="City", readonly=True)
    customer_state_id = fields.Many2one(related="customer_id.state_id", string="State", readonly=True)
    customer_zip = fields.Char(related="customer_id.zip", string="Zip", readonly=True)
    customer_country_id = fields.Many2one(related="customer_id.country_id", string="Country", readonly=True)

    # Line items and progress fields
    line_ids = fields.One2many("test.sample.line", "sample_id", string="Items", help="Individual items in this batch. Each line tracks one physical item through the full testing workflow.")
    notes = fields.Text(string="Internal Notes", help="Internal notes about this batch. Not visible to the customer.")
    has_incoming_lines = fields.Boolean(compute="_compute_line_state_flags", help="True if at least one item in the batch is still in Incoming state.")
    has_testable_lines = fields.Boolean(compute="_compute_line_state_flags", help="True if at least one item is currently In Testing.")
    has_transferable_lines = fields.Boolean(compute="_compute_line_state_flags", help="True if at least one In Testing item has passed inspection and has no pending transfer.")
    has_returnable_lines = fields.Boolean(compute="_compute_line_state_flags", help="True if at least one item is in Tested state and ready to be returned.")
    progress_summary = fields.Char(string="Progress", compute="_compute_progress_summary", store=True, help="Short summary of item counts by state, shown under the batch reference.")

    @api.model_create_multi
    def create(self, vals_list):
        # Auto-assign a sequential reference number (e.g. SMPL/2026/0001) on first save
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("test.sample") or "New"
        return super().create(vals_list)

    def action_mark_all_received(self):
        # Opens the bulk-receive wizard to confirm quantities for all incoming items at once
        self.ensure_one()
        return {
            "name": _("Receive Items"),
            "type": "ir.actions.act_window",
            "res_model": "test.sample.bulk.receive.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_sample_id": self.id},
        }

    def action_open_bulk_transfer_wizard(self):
        # Opens the bulk-transfer wizard to move all eligible items to a new location and owner at once
        self.ensure_one()
        return {
            "name": _("Bulk Transfer Items"),
            "type": "ir.actions.act_window",
            "res_model": "test.sample.bulk.transfer.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_sample_id": self.id},
        }

    def action_open_bulk_return_wizard(self):
        # Opens the bulk-return wizard to submit return requests for all tested items at once
        self.ensure_one()
        return {
            "name": _("Bulk Return Items"),
            "type": "ir.actions.act_window",
            "res_model": "test.sample.bulk.return.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_sample_id": self.id},
        }

    def action_mark_all_tested(self):
        # Move every item currently in testing to Tested in one click.
        # Respects the responsible user check on each line.
        self.ensure_one()
        in_testing = self.line_ids.filtered(lambda l: l.state == "in_testing")
        for line in in_testing:
            line._assert_is_responsible(_("mark this item as tested"))
        in_testing.write({"state": "tested"})

    @api.depends("line_ids.state", "line_ids.inspection_result", "line_ids.has_pending_transfer")
    def _compute_line_state_flags(self):
        # Computes all four button-visibility flags in a single pass over line_ids.
        for sample in self:
            incoming = testable = transferable = returnable = False
            for l in sample.line_ids:
                s = l.state
                if s == "incoming":
                    incoming = True
                if s == "in_testing":
                    testable = True
                    if l.inspection_result == "good" and not l.has_pending_transfer:
                        transferable = True
                if s == "received" and not l.has_pending_transfer:
                    transferable = True
                if s == "tested":
                    returnable = True
                if incoming and testable and transferable and returnable:
                    break
            sample.has_incoming_lines = incoming
            sample.has_testable_lines = testable
            sample.has_transferable_lines = transferable
            sample.has_returnable_lines = returnable

    @api.depends("line_ids.state")
    def _compute_progress_summary(self):
        # Builds a one-line status summary shown on kanban cards, e.g. "3/5 in testing or beyond"
        for sample in self:
            lines = sample.line_ids
            total = len(lines)
            if not total:
                sample.progress_summary = _("No items")
                continue
            closed = sum(1 for l in lines if l.state in ("returned", "scrapped"))
            tested = sum(1 for l in lines if l.state in ("tested", "scrap_pending"))
            in_testing = sum(1 for l in lines if l.state in ("in_testing", "in_transfer"))
            received = sum(1 for l in lines if l.state == "received")
            if closed == total:
                sample.progress_summary = _("All closed (%d)") % total
            elif closed + tested == total:
                sample.progress_summary = _("All tested, pending closure")
            elif in_testing + tested + closed > 0:
                sample.progress_summary = _("%d/%d in testing or beyond") % (in_testing + tested + closed, total)
            elif received > 0:
                sample.progress_summary = _("%d/%d received") % (received, total)
            else:
                sample.progress_summary = _("%d item(s) incoming") % total
