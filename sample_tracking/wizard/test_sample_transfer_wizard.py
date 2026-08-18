from odoo import models, fields
from odoo.exceptions import UserError
from odoo.tools.translate import _
from markupsafe import Markup


# Popup form to hand a sample item off to another user and/or location.
# Creates a transfer record and notifies the recipient with an activity.
class TestSampleTransferWizard(models.TransientModel):
    _name = "test.sample.transfer.wizard"
    _description = "Transfer Sample Wizard"

    line_id = fields.Many2one(
        "test.sample.line",
        string="Sample Item",
        required=True,
        ondelete="cascade",
        help="The item being transferred.",
    )
    from_location_id = fields.Many2one(
        "test.sample.location",
        string="Current Location",
        related="line_id.current_location_id",
        readonly=True,
        help="Where the item is now. Read-only, pulled from the item record.",
    )
    to_location_id = fields.Many2one(
        "test.sample.location",
        string="Transfer To",
        required=True,
        help="Location the item will be moved to.",
    )
    to_user_id = fields.Many2one(
        "res.users",
        string="New Owner",
        required=True,
        help="User who will take responsibility for the item. They will receive an activity notification and must accept the transfer.",
    )
    task_id = fields.Many2one(
        "project.task",
        string="Task",
        help="Optional: link this item to a task on the related sale order. Visible on the item record after the transfer is accepted.",
    )
    notes = fields.Text(string="Notes", help="Optional notes for the recipient, for example handling instructions or testing context.")

    def action_confirm(self):
        # Re-validate, create the transfer record, set the item to In Transfer,
        # schedule an activity for the recipient, and post a chatter message
        line = self.line_id
        line._assert_is_responsible(_("initiate a transfer"))
        if line.state not in ("received", "in_testing"):
            raise UserError(_("Only items in Received or In Testing state can be transferred."))
        if line.state == "in_testing" and line.inspection_result != "good":
            raise UserError(
                _("Item must pass inspection (Good) before it can be transferred from In Testing.")
            )
        self.env["test.sample.transfer"].create({
            "line_id": line.id,
            "from_location_id": line.current_location_id.id if line.current_location_id else False,
            "to_location_id": self.to_location_id.id,
            "to_user_id": self.to_user_id.id,
            "notes": self.notes,
            "state": "pending",
        })
        vals = {"pre_transfer_state": line.state, "state": "in_transfer"}
        if self.task_id:
            vals["task_id"] = self.task_id.id
        line.write(vals)
        line.activity_schedule(
            "mail.mail_activity_data_todo",
            user_id=self.to_user_id.id,
            summary=_("Pending Transfer: %s") % line.product_name,
            note=Markup(
                _("<b>%(item)s</b> has been transferred to you by %(user)s. "
                  "<a href='/odoo/action-sample_tracking.action_my_sample_items'>Open My Items</a> "
                  "and click <em>Accept Transfer</em> to take ownership.")
            ) % {"item": line.product_name, "user": self.env.user.name},
        )
        line.sample_id.message_post(
            body=Markup(
                _("<b>Transfer pending</b>: <em>%(item)s</em> has been assigned to %(user)s "
                  "and is awaiting acceptance.")
            ) % {"item": line.product_name, "user": self.to_user_id.name},
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        return {"type": "ir.actions.act_window_close"}
