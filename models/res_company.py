from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    ocr_model_id = fields.Many2one(
        'muk_ai.model',
        string='Default OCR Model',
        domain=[('modality', '=', 'chat')],
    )
    ocr_sale_template_id = fields.Many2one(
        'tricasia.ocr.template',
        string='Default Sales OCR Template',
        domain=[('order_type', '=', 'sale')],
    )
    ocr_purchase_template_id = fields.Many2one(
        'tricasia.ocr.template',
        string='Default Purchase OCR Template',
        domain=[('order_type', '=', 'purchase')],
    )
