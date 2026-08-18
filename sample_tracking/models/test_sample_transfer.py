from odoo import models, fields
from odoo.exceptions import UserError
from odoo.tools.translate import _
from markupsafe import Markup


# Records the handover of a sample item from one person/location to another.
# The recipient must actively accept or decline. Nothing transfers automatically.
class TestSampleTransfer(models.Model):
    _name = "test.sample.transfer"
    _description = "Sample Transfer"
    _order = "date desc, id desc"

    line_id = fields.Many2one(
        "test.sample.line",
        string="Sample Item",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sample_id = fields.Many2one(
        "test.sample",
        string="Sample",
        related="line_id.sample_id",
        store=True,
        index=True,
    )
    from_location_id = fields.Many2one(
        "test.sample.location",
        string="From Location",
    )
    to_location_id = fields.Many2one(
        "test.sample.location",
        string="To Location",
        required=True,
    )
    date = fields.Datetime(
        string="Transfer Date",
        default=fields.Datetime.now,
        required=True,
    )
    responsible_id = fields.Many2one(
        "res.users",
        string="Transferred By",
        default=lambda self: self.env.user,
    )
    to_user_id = fields.Many2one(
        "res.users",
        string="New Owner",
        required=True,
    )
    state = fields.Selection(
        [("pending", "Pending"), ("done", "Done"), ("declined", "Declined"), ("cancelled", "Cancelled")],
        string="Status",
        default="pending",
        required=True,
    )
    notes = fields.Text(string="Notes")

    def action_accept_transfer(self):
        # Recipient confirms ownership: updates the item's location, responsible user, and restores its previous state
        for transfer in self:
            if not transfer.to_user_id:
                raise UserError(_("This transfer has no assigned recipient."))
            if self.env.user != transfer.to_user_id:
                raise UserError(
                    _("Only %s can accept this transfer.") % transfer.to_user_id.name
                )
            if transfer.state != "pending":
                raise UserError(_("This transfer has already been processed."))
            line = transfer.line_id
            # sudo: the accepting user becomes responsible but doesn't have
            # write access to the line yet (old responsible_id != env.user)
            line.sudo().write({
                "current_location_id": transfer.to_location_id.id,
                "responsible_id": transfer.to_user_id.id,
                "state": line.pre_transfer_state or "in_testing",
                "pre_transfer_state": False,
            })
            transfer.write({"state": "done"})
            # Close the pending-transfer activity created for the recipient
            activities = line.activity_ids.filtered(
                lambda a: a.user_id == transfer.to_user_id
            )
            if activities:
                activities.action_done()

    def action_decline_transfer(self):
        # Recipient refuses the handover, item reverts to its previous state and the sender is notified
        for transfer in self:
            if self.env.user != transfer.to_user_id:
                raise UserError(
                    _("Only %s can decline this transfer.") % transfer.to_user_id.name
                )
            if transfer.state != "pending":
                raise UserError(_("This transfer has already been processed."))
            transfer.write({"state": "declined"})
            line = transfer.line_id
            line.sudo().write({
                "state": line.pre_transfer_state or "in_testing",
                "pre_transfer_state": False,
            })
            # Close the pending-transfer activity for the recipient
            activities = line.activity_ids.filtered(
                lambda a: a.user_id == transfer.to_user_id
            )
            if activities:
                activities.action_done()
            # Notify the sender
            line.sample_id.message_post(
                body=Markup(
                    _("<b>Transfer declined</b>: <em>%(item)s</em>. %(recipient)s declined the transfer "
                      "initiated by %(sender)s.")
                ) % {
                    "item": line.product_name,
                    "recipient": transfer.to_user_id.name,
                    "sender": transfer.responsible_id.name,
                },
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )

    def action_cancel_transfer(self):
        # Sender withdraws the transfer before the recipient has acted, item reverts to its previous state
        for transfer in self:
            if self.env.user != transfer.responsible_id:
                raise UserError(
                    _("Only %s can cancel this transfer.") % transfer.responsible_id.name
                )
            if transfer.state != "pending":
                raise UserError(_("Only pending transfers can be cancelled."))
            transfer.write({"state": "cancelled"})
            line = transfer.line_id
            line.sudo().write({
                "state": line.pre_transfer_state or "in_testing",
                "pre_transfer_state": False,
            })
            # Close the pending-transfer activity for the recipient
            activities = line.activity_ids.filtered(
                lambda a: a.user_id == transfer.to_user_id
            )
            if activities:
                activities.action_done()
            line.sample_id.message_post(
                body=Markup(
                    _("<b>Transfer cancelled</b>: <em>%(item)s</em>, transfer to %(recipient)s "
                      "was cancelled by %(sender)s.")
                ) % {
                    "item": line.product_name,
                    "recipient": transfer.to_user_id.name,
                    "sender": transfer.responsible_id.name,
                },
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
