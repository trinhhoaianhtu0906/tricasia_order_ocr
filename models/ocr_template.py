from odoo import fields, models

ORDER_TYPES = [('sale', 'Sales Order'), ('purchase', 'Purchase Order')]


class OcrTemplate(models.Model):
    """How one kind of customer or vendor document is read into an order."""

    _name = 'tricasia.ocr.template'
    _description = 'Order OCR Template'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        'res.company',
        default=lambda self: self.env.company,
    )
    order_type = fields.Selection(
        ORDER_TYPES,
        string='Applies To',
        required=True,
        default='sale',
    )
    model_id = fields.Many2one(
        'muk_ai.model',
        string='AI Model',
        domain=[('modality', '=', 'chat')],
        help='Leave empty to use the default OCR model from the settings.',
    )
    input_mode = fields.Selection(
        [('file', 'Send the PDF file'), ('text', 'Send the extracted text')],
        string='Read PDF As',
        required=True,
        default='file',
        help=(
            'Send the PDF file: the model reads the document itself, scans '
            'included; needs a model that accepts files (OpenAI, Anthropic, '
            'Google, Mistral, OpenRouter).\n'
            'Send the extracted text: cheaper and works with any model, '
            'Ollama included, but only for PDFs that carry a text layer.'
        ),
    )
    instructions = fields.Text(
        help=(
            'Hints for this document layout, added to the extraction prompt. '
            'E.g. "Item codes are in the second column; prices include VAT."'
        ),
    )
    default_partner_id = fields.Many2one(
        'res.partner',
        string='Default Partner',
        help='Customer or vendor used when the document partner is not found.',
    )
    default_product_id = fields.Many2one(
        'product.product',
        string='Default Product',
        help='Product used for a line whose product is not found.',
    )

    def _get_model(self):
        """Return the AI model of the template, or the company default."""
        self.ensure_one()
        return self.model_id or self.env.company.ocr_model_id
