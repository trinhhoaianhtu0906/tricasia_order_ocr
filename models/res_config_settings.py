from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ocr_model_id = fields.Many2one(
        related='company_id.ocr_model_id',
        readonly=False,
    )
    ocr_sale_template_id = fields.Many2one(
        related='company_id.ocr_sale_template_id',
        readonly=False,
    )
    ocr_purchase_template_id = fields.Many2one(
        related='company_id.ocr_purchase_template_id',
        readonly=False,
    )
