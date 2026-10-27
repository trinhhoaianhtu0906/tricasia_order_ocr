import base64
import logging
from datetime import datetime

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import plaintext2html
from odoo.tools.mimetypes import guess_mimetype

from odoo.addons.tricasia_order_ocr.models.ocr_template import ORDER_TYPES
from odoo.addons.tricasia_order_ocr.tools import (
    EXTRACTION_SCHEMA,
    build_inputs,
    parse_extraction,
)

_logger = logging.getLogger(__name__)

ACCEPTED_MIMETYPES = ('application/pdf', 'image/png', 'image/jpeg', 'image/webp')
SCHEMA_ERROR_TOKENS = ('schema', 'response_format', 'json', 'structured', 'format')
DISPLAY_TYPES = {'section': 'line_section', 'note': 'line_note'}


class OrderOcrWizard(models.TransientModel):
    """Read a document with an AI model, let the user review it, then create the order."""

    _name = 'tricasia.order.ocr.wizard'
    _description = 'Import Order with OCR'

    state = fields.Selection(
        [('upload', 'Upload'), ('review', 'Review')],
        default='upload',
        required=True,
    )
    order_type = fields.Selection(ORDER_TYPES, required=True, default='sale')
    template_id = fields.Many2one(
        'tricasia.ocr.template',
        string='Template',
        required=True,
        domain="[('order_type', '=', order_type)]",
        compute='_compute_template_id',
        store=True,
        precompute=True,
        readonly=False,
    )
    ai_model_id = fields.Many2one(
        'muk_ai.model',
        string='AI Model',
        compute='_compute_ai_model_id',
    )
    file = fields.Binary(string='Document', required=True, attachment=False)
    filename = fields.Char()
    attachment_id = fields.Many2one('ir.attachment')

    partner_id = fields.Many2one('res.partner', string='Partner')
    partner_raw = fields.Char(string='Partner (document)', readonly=True)
    partner_matched = fields.Boolean(readonly=True)
    reference = fields.Char()
    date_order = fields.Datetime(string='Order Date')
    date_delivery = fields.Datetime(string='Delivery Date')
    currency_id = fields.Many2one('res.currency', string='Currency')
    currency_raw = fields.Char(string='Currency (document)', readonly=True)
    note = fields.Text()
    line_ids = fields.One2many('tricasia.order.ocr.wizard.line', 'wizard_id', string='Lines')
    unmatched_count = fields.Integer(compute='_compute_unmatched_count')

    # ----------------------------------------------------------
    # Compute
    # ----------------------------------------------------------

    @api.depends('order_type')
    def _compute_template_id(self):
        company = self.env.company
        for wizard in self:
            if wizard.template_id.order_type == wizard.order_type:
                continue
            default = company[f'ocr_{wizard.order_type}_template_id']
            wizard.template_id = default or self.env['tricasia.ocr.template'].search(
                [('order_type', '=', wizard.order_type)], limit=1
            )

    @api.depends('template_id.model_id')
    def _compute_ai_model_id(self):
        for wizard in self:
            wizard.ai_model_id = (
                wizard.template_id._get_model() if wizard.template_id
                else self.env.company.ocr_model_id
            )

    @api.depends('line_ids.matched', 'partner_matched', 'state')
    def _compute_unmatched_count(self):
        for wizard in self:
            products = wizard.line_ids.filtered(lambda l: not l.display_type)
            wizard.unmatched_count = len(products.filtered(lambda l: not l.matched)) + (
                0 if wizard.partner_matched or wizard.state != 'review' else 1
            )

    # ----------------------------------------------------------
    # Extraction
    # ----------------------------------------------------------

    def _store_document(self):
        """Keep the upload as an attachment, which muk_ai materializes for the model.

        :raise UserError: for a file that is neither a PDF nor an image
        """
        raw = base64.b64decode(self.file or b'')
        mimetype = guess_mimetype(raw)
        if mimetype not in ACCEPTED_MIMETYPES:
            raise UserError(_('Upload a PDF or an image (PNG, JPEG, WebP), not %s.', mimetype))
        self.attachment_id.unlink()
        self.attachment_id = self.env['ir.attachment'].create(
            {
                'name': self.filename or 'document.pdf',
                'raw': raw,
                'mimetype': mimetype,
                'res_model': self._name,
                'res_id': self.id,
            }
        )
        return self.attachment_id

    def _document_block(self, attachment):
        """Return the content block carrying the document in the template's input mode.

        :raise UserError: when text mode finds no text layer to send
        """
        if self.template_id.input_mode == 'file' or attachment.mimetype != 'application/pdf':
            return attachment._ai_materialize()
        text = (attachment._index(attachment.raw, attachment.mimetype) or '').strip()
        if not text:
            raise UserError(_(
                'This PDF has no text layer (it is probably a scan). Set the '
                'template to "Send the PDF file" and pick a model that reads files.'
            ))
        return {'type': 'input_text', 'text': f'--- Document: {attachment.name} ---\n{text}'}

    def _call_model(self, ai_model, inputs):
        """Ask the model for the extraction, retrying without the schema if it is refused.

        Structured output is not served by every vendor and model; when the
        request fails over it, the prompt alone still asks for the JSON.
        """
        provider = ai_model.provider_id
        try:
            return provider._request_responses(
                inputs, text_schema=EXTRACTION_SCHEMA, model=ai_model.technical_name
            )
        except UserError as error:
            if not any(token in str(error).lower() for token in SCHEMA_ERROR_TOKENS):
                raise
            _logger.info('Model %s refused the extraction schema, retrying without it: %s',
                         ai_model.technical_name, error)
        return provider._request_responses(inputs, model=ai_model.technical_name)

    def action_extract(self):
        """Send the document to the AI model and load the result for review."""
        self.ensure_one()
        ai_model = self.template_id._get_model()
        if not ai_model:
            raise UserError(_(
                'No AI model is set. Pick one on the template, or set the default '
                'OCR model in Settings > MuK AI > Order OCR.'
            ))
        if not ai_model.provider_id._can_serve():
            raise UserError(_(
                'The %(provider)s provider is not configured: add its API key in '
                'MuK AI > Providers.', provider=ai_model.provider_id.display_name,
            ))
        attachment = self._store_document()
        inputs = build_inputs(
            self.order_type, self.template_id.instructions, self._document_block(attachment)
        )
        result = self._call_model(ai_model, inputs)
        try:
            data = parse_extraction(result.get('text'))
        except ValueError as error:
            raise UserError(_('The AI model did not return readable order data:\n%s', error)) from error
        self._load_extraction(data)
        return self._reopen()

    # ----------------------------------------------------------
    # Mapping
    # ----------------------------------------------------------

    def _load_extraction(self, data):
        """Map the extracted dict onto the review fields, falling back to template defaults."""
        partner = self._match_partner(data.get('partner_name'), data.get('partner_vat'))
        currency = self._match_currency(data.get('currency'))
        self.write({
            'state': 'review',
            'partner_id': (partner or self.template_id.default_partner_id).id,
            'partner_matched': bool(partner),
            'partner_raw': ' / '.join(
                str(value) for value in (data.get('partner_name'), data.get('partner_vat')) if value
            ),
            'reference': data.get('reference') or False,
            'date_order': self._parse_date(data.get('order_date')),
            'date_delivery': self._parse_date(data.get('delivery_date')),
            'currency_id': (currency or self.env.company.currency_id).id,
            'currency_raw': data.get('currency') or False,
            'note': data.get('note') or False,
            'line_ids': [Command.clear()] + [
                Command.create(self._line_values(line, partner, sequence))
                for sequence, line in enumerate(data['lines'], start=1)
            ],
        })

    def _line_values(self, line, partner, sequence):
        """Return the review-line values of one extracted line."""
        description = (line.get('description') or '').strip()
        display_type = DISPLAY_TYPES.get(line.get('type'))
        if display_type:
            return {'sequence': sequence * 10, 'display_type': display_type, 'name': description or '-'}
        code = (line.get('code') or '').strip()
        product = self._match_product(code, description, partner)
        fallback = product or self.template_id.default_product_id
        price = line.get('unit_price')
        if price is None and fallback:
            price = fallback.lst_price if self.order_type == 'sale' else fallback.standard_price
        name = ' '.join(part for part in (f'[{code}]' if code else '', description) if part)
        return {
            'sequence': sequence * 10,
            'product_id': fallback.id,
            'matched': bool(product),
            'product_raw': name,
            'name': description or (fallback.display_name if fallback else '') or code or '-',
            'quantity': line.get('quantity') or 1.0,
            'uom_id': (self._match_uom(line.get('uom')) or fallback.uom_id).id,
            'price_unit': price or 0.0,
            'discount': line.get('discount') or 0.0,
            'tax_ids': [Command.set(self._taxes_for(line.get('tax_percent'), fallback).ids)],
        }

    def _match_partner(self, name, vat):
        """Find the partner by tax ID first, then by exact name, then by a unique partial name."""
        Partner = self.env['res.partner']
        company_domain = [('company_id', 'in', [False, self.env.company.id])]
        vat = ''.join(ch for ch in (vat or '') if ch.isalnum())
        if vat:
            partner = Partner.search(company_domain + [('vat', 'ilike', vat)], limit=1)
            if partner:
                return partner.commercial_partner_id
        name = (name or '').strip()
        if not name:
            return Partner
        partner = Partner.search(company_domain + [('name', '=ilike', name)], limit=1)
        if not partner:
            candidates = Partner.search(
                company_domain + [('name', 'ilike', name), ('is_company', '=', True)], limit=2
            )
            partner = candidates if len(candidates) == 1 else Partner
        return partner.commercial_partner_id

    def _match_product(self, code, description, partner):
        """Find the product by internal code, barcode, vendor code (purchases), then name."""
        Product = self.env['product.product']
        domain = [('sale_ok' if self.order_type == 'sale' else 'purchase_ok', '=', True)]
        if code:
            product = (
                Product.search(domain + [('default_code', '=ilike', code)], limit=1)
                or Product.search(domain + [('barcode', '=', code)], limit=1)
            )
            if not product and self.order_type == 'purchase':
                product = self._match_vendor_code(code, partner)
            if product:
                return product
        if description:
            return Product.search(domain + [('name', '=ilike', description)], limit=1)
        return Product

    def _match_vendor_code(self, code, partner):
        """Return the product a vendor price list entry names with ``code``."""
        domain = [('product_code', '=ilike', code)]
        if partner:
            domain.append(('partner_id', '=', partner.id))
        seller = self.env['product.supplierinfo'].search(domain, limit=1)
        return seller.product_id or seller.product_tmpl_id.product_variant_id

    def _match_uom(self, name):
        name = (name or '').strip()
        return self.env['uom.uom'].search([('name', '=ilike', name)], limit=1) if name else self.env['uom.uom']

    def _match_currency(self, code):
        code = (code or '').strip()
        return self.env['res.currency'].search([('name', '=ilike', code)], limit=1) if code else self.env['res.currency']

    def _taxes_for(self, percent, product):
        """Return the company tax of that rate, else the product's default taxes."""
        scope = 'sale' if self.order_type == 'sale' else 'purchase'
        company = self.env.company
        if percent is not None:
            tax = self.env['account.tax'].search([
                ('type_tax_use', '=', scope),
                ('amount_type', '=', 'percent'),
                ('amount', '=', float(percent)),
                ('company_id', 'parent_of', company.id),
            ], limit=1)
            if tax:
                return tax
        taxes = product.taxes_id if scope == 'sale' else product.supplier_taxes_id
        return taxes.filtered(lambda tax: tax.company_id in company.parent_ids)

    @staticmethod
    def _parse_date(value):
        try:
            return datetime.strptime((value or '')[:10], '%Y-%m-%d')
        except ValueError:
            return False

    # ----------------------------------------------------------
    # Order
    # ----------------------------------------------------------

    def _order_line_commands(self):
        """Return the order line commands in review order, sections and notes included."""
        qty_field = 'product_uom_qty' if self.order_type == 'sale' else 'product_qty'
        commands = []
        for line in self.line_ids.sorted('sequence'):
            values = {'sequence': line.sequence, 'name': line.name}
            if line.display_type:
                values['display_type'] = line.display_type
                if self.order_type == 'purchase':
                    values[qty_field] = 0.0
            else:
                if not line.product_id:
                    raise UserError(_('Pick a product for the line "%s".', line.name))
                values.update({
                    'product_id': line.product_id.id,
                    qty_field: line.quantity,
                    'product_uom_id': (line.uom_id or line.product_id.uom_id).id,
                    'price_unit': line.price_unit,
                    'discount': line.discount,
                    'tax_ids': [Command.set(line.tax_ids.ids)],
                })
                if self.order_type == 'purchase' and self.date_delivery:
                    values['date_planned'] = self.date_delivery
            commands.append(Command.create(values))
        return commands

    def _order_values(self):
        """Return the header values of the order to create."""
        values = {
            'partner_id': self.partner_id.id,
            'order_line': self._order_line_commands(),
        }
        if self.note:
            values['note'] = plaintext2html(self.note)
        if self.date_order:
            values['date_order'] = self.date_order
        if self.order_type == 'sale':
            values['client_order_ref'] = self.reference
            if self.date_delivery:
                values['commitment_date'] = self.date_delivery
            if self.currency_id and self.currency_id != self.env.company.currency_id:
                pricelist = self.env['product.pricelist'].search(
                    [('currency_id', '=', self.currency_id.id),
                     ('company_id', 'in', [False, self.env.company.id])], limit=1
                )
                if pricelist:
                    values['pricelist_id'] = pricelist.id
        else:
            values['partner_ref'] = self.reference
            if self.currency_id:
                values['currency_id'] = self.currency_id.id
        return values

    def action_create_order(self):
        """Create the draft order, file the document on it and open it."""
        self.ensure_one()
        if not self.partner_id:
            raise UserError(_('Pick a partner, or set a default partner on the template.'))
        model = 'sale.order' if self.order_type == 'sale' else 'purchase.order'
        order = self.env[model].create(self._order_values())
        self.attachment_id.write({'res_model': model, 'res_id': order.id})
        order.message_post(
            body=_(
                'Created from %(file)s with OCR template %(template)s and model %(model)s.',
                file=self.filename or self.attachment_id.name,
                template=self.template_id.display_name,
                model=self.ai_model_id.display_name,
            ),
            attachment_ids=self.attachment_id.ids,
        )
        return {
            'type': 'ir.actions.act_window',
            'res_model': model,
            'res_id': order.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_back(self):
        self.state = 'upload'
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'name': _('Import with OCR'),
        }


class OrderOcrWizardLine(models.TransientModel):
    _name = 'tricasia.order.ocr.wizard.line'
    _description = 'Import Order with OCR - Line'
    _order = 'sequence, id'

    wizard_id = fields.Many2one('tricasia.order.ocr.wizard', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    display_type = fields.Selection([('line_section', 'Section'), ('line_note', 'Note')])
    product_id = fields.Many2one('product.product', string='Product')
    matched = fields.Boolean(readonly=True)
    product_raw = fields.Char(string='Document Item', readonly=True)
    name = fields.Text(string='Description', required=True)
    quantity = fields.Float(default=1.0, digits='Product Unit')
    uom_id = fields.Many2one('uom.uom', string='Unit')
    price_unit = fields.Float(string='Unit Price', digits='Product Price')
    discount = fields.Float(string='Disc.%', digits='Discount')
    tax_ids = fields.Many2many('account.tax', string='Taxes')

    @api.onchange('product_id')
    def _onchange_product_id(self):
        """A product picked by hand counts as matched and brings its unit."""
        if self.product_id:
            self.matched = True
            self.uom_id = self.uom_id or self.product_id.uom_id
