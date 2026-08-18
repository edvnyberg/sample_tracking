from odoo import models, fields
from odoo.exceptions import UserError
from odoo.tools.translate import _
from markupsafe import Markup


# An approval request raised when a non-manager wants to dispose of (scrap) a sample item.
# A manager must approve or reject it before the item is actually marked as scrapped.
class TestSampleScrapRequest(models.Model):
    _name = "test.sample.scrap.request"
    _description = "Sample Scrap Request"
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
    product_name = fields.Char(
        related="line_id.product_name",
        string="Product / Item",
        store=True,
    )
    requested_by_id = fields.Many2one(
        "res.users",
        string="Requested By",
        default=lambda self: self.env.user,
        required=True,
    )
    approver_id = fields.Many2one(
        "res.users",
        string="Approver",
    )
    reviewed_by_id = fields.Many2one(
        "res.users",
        string="Reviewed By",
    )
    date = fields.Datetime(
        string="Request Date",
        default=fields.Datetime.now,
        required=True,
    )
    date_reviewed = fields.Datetime(string="Review Date")
    reason = fields.Text(string="Reason for Scrapping")
    rejection_reason = fields.Text(string="Rejection Reason")
    state = fields.Selection(
        [
            ("pending", "Pending Approval"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("cancelled", "Cancelled"),
        ],
        string="Status",
        default="pending",
        required=True,
    )

    def action_approve(self):
        # Manager approves: item is marked Scrapped and the person who requested it is notified
        self.ensure_one()
        if self.state != "pending":
            raise UserError(_("Only pending requests can be approved."))
        if self.line_id.state not in ("tested", "scrap_pending"):
            raise UserError(_("The item is no longer in a scrap-eligible state."))
        self.write({
            "state": "approved",
            "reviewed_by_id": self.env.user.id,
            "date_reviewed": fields.Datetime.now(),
        })
        self.line_id.write({
            "state": "scrapped",
            "date_closed": fields.Date.today(),
        })
        self.line_id.sample_id.message_post(
            body=Markup(
                _("<b>Scrap approved</b>: <em>%(item)s</em> has been scrapped. Approved by %(user)s.")
            ) % {"item": self.line_id.product_name, "user": self.env.user.name},
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        self._send_approval_email()

    def action_reject(self):
        # Manager rejects: item goes back to Tested so the requester can decide what to do next
        self.ensure_one()
        if self.state != "pending":
            raise UserError(_("Only pending requests can be rejected."))
        self.write({
            "state": "rejected",
            "reviewed_by_id": self.env.user.id,
            "date_reviewed": fields.Datetime.now(),
        })
        self.line_id.sudo().write({"state": "tested"})
        self.line_id.sample_id.message_post(
            body=Markup(
                _("<b>Scrap rejected</b>: Scrap request for <em>%(item)s</em> was rejected by %(user)s.")
            ) % {"item": self.line_id.product_name, "user": self.env.user.name},
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )

    def action_cancel(self):
        # Requester withdraws their own scrap request, item goes back to Tested
        self.ensure_one()
        if self.env.user != self.requested_by_id:
            raise UserError(
                _("Only %s can cancel this scrap request.") % self.requested_by_id.name
            )
        if self.state != "pending":
            raise UserError(_("Only pending requests can be cancelled."))
        self.write({"state": "cancelled"})
        self.line_id.sudo().write({"state": "tested"})
        self.line_id.sample_id.message_post(
            body=Markup(
                _("<b>Scrap request cancelled</b>: Scrap request for <em>%(item)s</em> was cancelled by %(user)s.")
            ) % {"item": self.line_id.product_name, "user": self.env.user.name},
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )

    def _send_approval_email(self):
        # Send a direct email to the requester informing them the scrap was approved.
        requester = self.requested_by_id
        email_to = requester.partner_id.email or requester.login
        if not email_to:
            return
        line = self.line_id
        rows = [
            ("Item Ref", line.name),
            ("Item", line.product_name),
            ("Sample Ref", line.sample_id.name),
            ("Approved by", self.env.user.name),
            ("Date", fields.Date.today().strftime("%d %B %Y")),
        ]
        table_rows = "".join(
            "<tr>"
            "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
            "<strong>%s</strong>"
            "</td>"
            "<td style='padding: 6px 0; color: #222;'>%s</td>"
            "</tr>" % (label, value)
            for label, value in rows
        )
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        item_url = "%s/web#model=test.sample.line&id=%d&view_type=form" % (base_url, line.id)
        body_html = (
            "<div style='font-family: Arial, sans-serif; font-size: 14px; color: #222;'>"
            "<p style='margin-bottom: 16px;'>Hi %s,</p>"
            "<p style='margin-bottom: 16px;'>"
            "Your scrap request has been <strong>approved</strong>. "
            "The item can now be scrapped."
            "</p>"
            "<table style='border-collapse: collapse;'>%s</table>"
            "<p style='margin-top: 20px;'>"
            "<a href='%s' style='background: #875A7B; color: #fff; padding: 8px 16px; "
            "text-decoration: none; border-radius: 4px; font-weight: bold;'>View Item</a>"
            "</p>"
            "</div>"
        ) % (requester.name, table_rows, item_url)
        self.env["mail.mail"].sudo().create({
            "subject": "Scrap Request Approved – %s" % line.product_name,
            "email_from": self.env.company.email or "",
            "email_to": email_to,
            "body_html": body_html,
            "auto_delete": True,
        }).send()
