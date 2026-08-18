from odoo import models, fields


# One record per physical unit within a sample line.
# When a line has quantity=3, three unit records are auto-created with serials
# like ITEM/0001-01, ITEM/0001-02, ITEM/0001-03.
# Serials survive split and merge operations so each physical unit stays
# traceable regardless of which line it ends up on.
class TestSampleLineUnit(models.Model):
    _name = "test.sample.line.unit"
    _description = "Sample Line Unit Serial"
    _order = "serial_number"
    _rec_name = "serial_number"

    line_id = fields.Many2one(
        "test.sample.line",
        string="Item Line",
        required=True,
        ondelete="cascade",
        index=True,
    )
    serial_number = fields.Char(
        string="Serial Number",
        readonly=True,
        copy=False,
        help="Unique identifier for this individual physical unit. Assigned automatically and never changes.",
    )
    product_name = fields.Char(
        related="line_id.product_name",
        string="Item",
        readonly=True,
    )
