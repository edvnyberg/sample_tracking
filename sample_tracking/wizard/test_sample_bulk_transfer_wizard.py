from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.tools.translate import _
from markupsafe import Markup, escape


# Popup form to transfer all eligible items in a batch to a new location and/or owner in one step.
# Pre-fills with every item that is In Testing, passed inspection, and has no pending transfer.
class TestSampleBulkTransferWizard(models.TransientModel):
    _name = "test.sample.bulk.transfer.wizard"
    _description = "Bulk Transfer Items"

    sample_id = fields.Many2one("test.sample", string="Sample", readonly=True, help="The batch this wizard was opened from. Only set when opened from the sample form.")
    to_location_id = fields.Many2one("test.sample.location", string="Transfer To", required=True, help="Location all selected items will be moved to.")
    to_user_id = fields.Many2one(
        "res.users",
        string="New Owner",
        required=True,
        domain="[('active', '=', True), ('share', '=', False)]",
        help="User who will take responsibility for all selected items. They will receive one activity notification per item and must accept each transfer.",
    )
    notes = fields.Text(string="Notes", help="Optional notes sent to the recipient for all items in this bulk transfer.")
    line_ids = fields.One2many(
        "test.sample.bulk.transfer.wizard.line",
        "wizard_id",
        string="Items to Transfer",
        help="Items pre-selected for transfer. Uncheck any item you want to skip.",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        sample_id = self._context.get("default_sample_id")
        active_model = self._context.get("active_model")
        active_ids = self._context.get("active_ids", [])

        if sample_id:
            # Opened from a sample form: pre-fill with all eligible items on that batch.
            res["sample_id"] = sample_id
            sample = self.env["test.sample"].browse(sample_id)
            eligible = sample.line_ids.filtered(
                lambda l: l.state in ("received", "in_testing")
                and (l.state != "in_testing" or l.inspection_result == "good")
                and not l.has_pending_transfer
            )
        elif active_model == "test.sample.line" and active_ids:
            # Opened from a line list (e.g. My Items): use the selected rows.
            eligible = self.env["test.sample.line"].browse(active_ids).filtered(
                lambda l: l.state in ("received", "in_testing")
                and (l.state != "in_testing" or l.inspection_result == "good")
                and not l.has_pending_transfer
            )
        else:
            eligible = self.env["test.sample.line"]

        res["line_ids"] = [
            (0, 0, {
                "line_id": l.id,
                "product_name": l.product_name,
                "current_location_id": l.current_location_id.id if l.current_location_id else False,
                "include": True,
            })
            for l in eligible
        ]
        return res

    def action_confirm(self):
        self.ensure_one()
        selected = self.line_ids.filtered(lambda l: l.include)
        if not selected:
            raise UserError(_("Select at least one item to transfer."))

        for wline in selected:
            line = wline.line_id
            if line.state not in ("received", "in_testing") or (line.state == "in_testing" and line.inspection_result != "good") or line.has_pending_transfer:
                raise UserError(
                    _("%(item)s is no longer eligible for transfer. Refresh and try again.") % {"item": line.product_name}
                )
            self.env["test.sample.transfer"].create({
                "line_id": line.id,
                "from_location_id": line.current_location_id.id if line.current_location_id else False,
                "to_location_id": self.to_location_id.id,
                "to_user_id": self.to_user_id.id,
                "notes": self.notes,
                "state": "pending",
            })
            line.write({"pre_transfer_state": line.state, "state": "in_transfer"})

        # Send ONE consolidated email to the recipient listing all transferred items.
        self._send_bulk_transfer_notification(selected)

        if self.sample_id:
            self.sample_id.message_post(
                body=Markup(
                    _("<b>Bulk transfer pending</b>: %(count)d item(s) assigned to %(user)s and awaiting acceptance.")
                ) % {"count": len(selected), "user": self.to_user_id.name},
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
        else:
            # Opened from a line list: post a summary on each affected batch.
            for sample in selected.mapped("line_id.sample_id"):
                batch_count = len(selected.filtered(lambda wl: wl.line_id.sample_id == sample))
                sample.message_post(
                    body=Markup(
                        _("<b>Bulk transfer pending</b>: %(count)d item(s) assigned to %(user)s and awaiting acceptance.")
                    ) % {"count": batch_count, "user": self.to_user_id.name},
                    message_type="comment",
                    subtype_xmlid="mail.mt_comment",
                )
        return {"type": "ir.actions.act_window_close"}

    def _send_bulk_transfer_notification(self, selected):
        # Send one summary email to the recipient listing all transferred items.
        recipient = self.to_user_id
        email_to = (recipient.partner_id.email or recipient.login or "").strip()
        if not email_to:
            return
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        my_items_url = "%s/odoo/action-sample_tracking.action_my_sample_items" % base_url
        row_parts = []
        for wl in selected:
            line = wl.line_id
            item_url = "%s/web#model=test.sample.line&id=%d&view_type=form" % (base_url, line.id)
            row_parts.append(Markup(
                "<tr>"
                "<td style='padding: 6px 8px; border-bottom: 1px solid #eee;'>"
                "<a href='{url}'>{product}</a>"
                "</td>"
                "<td style='padding: 6px 8px; border-bottom: 1px solid #eee; color: #555;'>{ref}</td>"
                "<td style='padding: 6px 8px; border-bottom: 1px solid #eee; color: #555;'>{sample}</td>"
                "<td style='padding: 6px 8px; border-bottom: 1px solid #eee; color: #555;'>{so}</td>"
                "</tr>"
            ).format(
                url=item_url,
                product=escape(line.product_name),
                ref=escape(line.name or ""),
                sample=escape(line.sample_id.name or ""),
                so=escape(line.sale_order_id.name or ""),
            ))
        item_rows = Markup("").join(row_parts)
        notes_html = (
            Markup("<p style='color: #555; margin-bottom: 12px;'><strong>Notes:</strong> {}</p>").format(escape(self.notes))
        ) if self.notes else Markup("")
        body_html = Markup(
            "<div style='font-family: Arial, sans-serif; font-size: 14px; color: #222;'>"
            "<p>Hi {recipient},</p>"
            "<p><strong>{count} item(s)</strong> have been transferred to you by <strong>{sender}</strong> "
            "to <strong>{location}</strong>. Please accept each transfer in <em>My Items</em>.</p>"
            "{notes}"
            "<table style='border-collapse: collapse; width: 100%; margin-bottom: 16px;'>"
            "<thead>"
            "<tr style='background: #f5f5f5;'>"
            "<th style='padding: 8px; text-align: left; border-bottom: 2px solid #ddd;'>Item</th>"
            "<th style='padding: 8px; text-align: left; border-bottom: 2px solid #ddd;'>Item Ref</th>"
            "<th style='padding: 8px; text-align: left; border-bottom: 2px solid #ddd;'>Sample Ref</th>"
            "<th style='padding: 8px; text-align: left; border-bottom: 2px solid #ddd;'>Sale Order</th>"
            "</tr>"
            "</thead>"
            "<tbody>{rows}</tbody>"
            "</table>"
            "<p>"
            "<a href='{items_url}' style='background: #875A7B; color: #fff; padding: 8px 16px; "
            "text-decoration: none; border-radius: 4px; font-weight: bold;'>Open My Items</a>"
            "</p>"
            "</div>"
        ).format(
            recipient=escape(recipient.name),
            count=len(selected),
            sender=escape(self.env.user.name),
            location=escape(self.to_location_id.name),
            notes=notes_html,
            rows=item_rows,
            items_url=my_items_url,
        )
        self.env["mail.mail"].sudo().create({
            "subject": "Pending Transfer: %d Item(s) Assigned to You" % len(selected),
            "email_from": self.env.company.email or "",
            "email_to": email_to,
            "body_html": body_html,
            "auto_delete": True,
        }).send()


class TestSampleBulkTransferWizardLine(models.TransientModel):
    _name = "test.sample.bulk.transfer.wizard.line"
    _description = "Bulk Transfer Item Line"

    wizard_id = fields.Many2one(
        "test.sample.bulk.transfer.wizard",
        required=True,
        ondelete="cascade",
    )
    line_id = fields.Many2one("test.sample.line", string="Item", required=True)
    product_name = fields.Char(string="Item")
    current_location_id = fields.Many2one("test.sample.location", string="Current Location")
    include = fields.Boolean(string="Include", default=True)
