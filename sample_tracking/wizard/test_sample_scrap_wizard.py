from odoo import models, fields, api, _
from odoo.exceptions import UserError
from markupsafe import Markup


# Popup form for a regular user to request that a tested item be scrapped.
# The user picks a manager as approver; that manager is notified and must approve or reject.
# Allows specifying moving assistance details (location, pallet status, notes) for logistics.
class TestSampleScrapWizard(models.TransientModel):
    _name = "test.sample.scrap.wizard"
    _description = "Request Scrap Approval"

    line_id = fields.Many2one("test.sample.line", required=True, ondelete="cascade", help="The item to be scrapped.")
    product_name = fields.Char(string="Product / Item", readonly=True, help="Name of the item, shown for reference.")
    approver_id = fields.Many2one(
        "res.users",
        string="Approver",
        required=True,
        domain=lambda self: [("groups_id", "in", self.env.ref("sample_tracking.group_sample_manager").ids)],
        help="Manager who must approve the scrap request. They will receive an activity notification and can approve or reject.",
    )
    
    requires_help_moving = fields.Boolean(string="Requires Help Moving", help="Tick if logistics needs to physically move this item and may need extra help.")
    moving_location_id = fields.Many2one("test.sample.location", string="Current Location", help="Where the item currently is. Pre-filled from the item's current location.")
    moving_notes = fields.Text(string="Moving Notes", help="Free-text instructions for logistics about moving this item (e.g. size, fragility, access constraints).")
    on_pallet = fields.Selection([("yes", "Yes"), ("no", "No")], string="On a Pallet?", help="Whether the item is on a pallet.")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        line_id = res.get("line_id") or self._context.get("default_line_id")
        if line_id:
            line = self.env["test.sample.line"].browse(line_id)
            res["product_name"] = line.product_name
            if line.current_location_id:
                res["moving_location_id"] = line.current_location_id.id
        if not res.get("approver_id"):
            approver_id = self.env["ir.config_parameter"].sudo().get_param(
                "sample_tracking.default_approver_id"
            )
            if approver_id:
                res["approver_id"] = int(approver_id)
        return res

    def action_confirm(self):
        # Create the scrap request record, put the item in Scrap Pending, and notify the approver
        self.ensure_one()
        line = self.line_id
        if line.state != "tested":
            raise UserError(_("Only tested items can be requested for scrapping."))
        if line.pending_scrap_request_id:
            raise UserError(_("This item already has a pending scrap request."))
        self.env["test.sample.scrap.request"].create({
            "line_id": line.id,
            "approver_id": self.approver_id.id,
        })
        line.write({"state": "scrap_pending"})
        line.sample_id.message_post(
            body=Markup(
                _("<b>Scrap requested</b>: %(user)s has requested to scrap <em>%(item)s</em>. "
                  "Awaiting approval from %(approver)s.")
            ) % {"user": self.env.user.name, "item": line.product_name, "approver": self.approver_id.name},
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        
        # Send moving help notification if needed
        if self.requires_help_moving:
            moving_info = {
                "location": self.moving_location_id.display_name if self.moving_location_id else "",
                "notes": self.moving_notes or "",
                "on_pallet": dict(self._fields["on_pallet"].selection).get(self.on_pallet, "") if self.on_pallet else "",
            }
            line._send_scrap_moving_notification(moving_info)
        
        return {"type": "ir.actions.act_window_close"}
