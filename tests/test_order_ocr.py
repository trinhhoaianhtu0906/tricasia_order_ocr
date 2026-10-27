import base64
import json
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

PDF = base64.b64encode(b'%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n')


@tagged('post_install', '-at_install')
class TestOrderOcr(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        provider = cls.env.ref('muk_ai.provider_openai')
        provider.sudo().api_key = 'test-key'
        cls.ai_model = cls.env['muk_ai.model'].search(
            [('provider_id', '=', provider.id), ('modality', '=', 'chat')], limit=1
        )
        cls.env.company.ocr_model_id = cls.ai_model
        cls.env['res.currency'].with_context(active_test=False).search(
            [('name', 'in', ['VND', 'USD'])]
        ).active = True
        cls.customer = cls.env['res.partner'].create(
            {'name': 'Cong ty ABC', 'is_company': True, 'vat': '0101234567'}
        )
        cls.vendor = cls.env['res.partner'].create({'name': 'Nha cung cap XYZ', 'is_company': True})
        cls.default_partner = cls.env['res.partner'].create({'name': 'Khach le', 'is_company': True})
        cls.default_product = cls.env['product.product'].create(
            {'name': 'San pham chung', 'list_price': 1.0, 'standard_price': 1.0}
        )
        cls.pen = cls.env['product.product'].create(
            {'name': 'But bi', 'default_code': 'PEN-01', 'list_price': 5000.0}
        )
        cls.paper = cls.env['product.product'].create({'name': 'Giay A4', 'barcode': '8930001'})
        cls.ink = cls.env['product.product'].create({'name': 'Muc in', 'standard_price': 70.0})
        cls.env['product.supplierinfo'].create({
            'partner_id': cls.vendor.id,
            'product_tmpl_id': cls.ink.product_tmpl_id.id,
            'product_code': 'XYZ-INK',
        })
        cls.tax_8 = cls.env['account.tax'].create(
            {'name': 'VAT 8% test', 'amount': 8.0, 'type_tax_use': 'sale', 'amount_type': 'percent'}
        )
        cls.sale_template = cls.env['tricasia.ocr.template'].create({
            'name': 'PO khach hang',
            'order_type': 'sale',
            'default_partner_id': cls.default_partner.id,
            'default_product_id': cls.default_product.id,
            'instructions': 'Ma hang o cot 2.',
        })
        cls.purchase_template = cls.env['tricasia.ocr.template'].create({
            'name': 'Bao gia NCC',
            'order_type': 'purchase',
            'default_partner_id': cls.default_partner.id,
            'default_product_id': cls.default_product.id,
        })

    def _wizard(self, order_type='sale', **values):
        return self.env['tricasia.order.ocr.wizard'].with_context(
            default_order_type=order_type
        ).create({'file': PDF, 'filename': 'po.pdf', **values})

    def _extract(self, wizard, reply, side_effect=None):
        calls = []

        def fake_request(provider, inputs, tools_schema=None, text_schema=None, on_delta=None,
                         model=None, **kwargs):
            calls.append({'inputs': inputs, 'text_schema': text_schema, 'model': model})
            if side_effect:
                side_effect(len(calls), text_schema)
            return {'text': reply if isinstance(reply, str) else json.dumps(reply)}

        with patch.object(self.registry['muk_ai.provider'], '_request_responses', fake_request):
            wizard.action_extract()
        return calls

    def _sale_reply(self):
        return {
            'partner_name': 'CONG TY ABC',
            'partner_vat': '0101 234 567',
            'reference': 'PO-2026-001',
            'order_date': '2026-10-20',
            'delivery_date': '2026-11-05',
            'currency': 'VND',
            'note': 'Thanh toan 30 ngay.\nGiao tai kho Ha Noi.',
            'lines': [
                {'type': 'section', 'description': 'Van phong pham', 'code': None, 'quantity': None,
                 'uom': None, 'unit_price': None, 'discount': None, 'tax_percent': None},
                {'type': 'product', 'code': 'pen-01', 'description': 'But bi xanh', 'quantity': 10,
                 'uom': 'Units', 'unit_price': 4500, 'discount': 5, 'tax_percent': 8},
                {'type': 'product', 'code': '8930001', 'description': 'Giay A4 70gsm', 'quantity': 2,
                 'uom': None, 'unit_price': None, 'discount': None, 'tax_percent': None},
                {'type': 'note', 'description': 'Hang giao 2 dot', 'code': None, 'quantity': None,
                 'uom': None, 'unit_price': None, 'discount': None, 'tax_percent': None},
                {'type': 'product', 'code': 'UNKNOWN-9', 'description': 'Hop dung but', 'quantity': 3,
                 'uom': 'Cai la', 'unit_price': 20000, 'discount': None, 'tax_percent': 99},
            ],
        }

    # ----------------------------------------------------------
    # Extraction and mapping
    # ----------------------------------------------------------

    def test_defaults_come_from_settings(self):
        self.env.company.ocr_sale_template_id = self.sale_template
        wizard = self._wizard()
        self.assertEqual(wizard.template_id, self.sale_template)
        self.assertEqual(wizard.ai_model_id, self.ai_model)
        self.assertEqual(self._wizard('purchase').template_id, self.purchase_template)

    def test_extract_maps_header_and_lines(self):
        wizard = self._wizard(template_id=self.sale_template.id)
        calls = self._extract(wizard, self._sale_reply())

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['model'], self.ai_model.technical_name)
        self.assertEqual(calls[0]['text_schema']['name'], 'order_extraction')
        system = calls[0]['inputs'][0]['content'][0]['text']
        self.assertIn('Ma hang o cot 2.', system)
        block = calls[0]['inputs'][1]['content'][1]
        self.assertEqual((block['strategy'], block['mimetype']), ('file', 'application/pdf'))

        self.assertEqual(wizard.state, 'review')
        self.assertEqual(json.loads(wizard.extracted_json), self._sale_reply())
        self.assertIn('"partner_name": "CONG TY ABC"', wizard.extracted_json)
        self.assertEqual(wizard.partner_id, self.customer)
        self.assertTrue(wizard.partner_matched)
        self.assertEqual(wizard.reference, 'PO-2026-001')
        self.assertEqual(str(wizard.date_delivery.date()), '2026-11-05')
        self.assertEqual(wizard.currency_id.name, 'VND')

        section, pen, paper, note, unknown = wizard.line_ids
        self.assertEqual((section.display_type, section.name), ('line_section', 'Van phong pham'))
        self.assertEqual((note.display_type, note.name), ('line_note', 'Hang giao 2 dot'))
        self.assertEqual(pen.product_id, self.pen)
        self.assertTrue(pen.matched)
        self.assertEqual((pen.quantity, pen.price_unit, pen.discount), (10, 4500, 5))
        self.assertEqual(pen.tax_ids.mapped('amount'), [8.0])
        self.assertEqual(pen.product_raw, '[pen-01] But bi xanh')
        self.assertEqual(paper.product_id, self.paper)
        self.assertEqual(paper.price_unit, self.paper.lst_price, 'a missing price falls back to the product')
        self.assertEqual(unknown.product_id, self.default_product)
        self.assertFalse(unknown.matched)
        self.assertEqual(unknown.name, 'Hop dung but')
        self.assertEqual(unknown.uom_id, self.default_product.uom_id)
        self.assertEqual(unknown.tax_ids, self.default_product.taxes_id.filtered(
            lambda t: t.company_id == self.env.company))
        self.assertEqual(wizard.unmatched_count, 1)

    def test_unknown_partner_falls_back(self):
        reply = {**self._sale_reply(), 'partner_name': 'Cong ty khong ton tai', 'partner_vat': None}
        wizard = self._wizard(template_id=self.sale_template.id)
        self._extract(wizard, reply)
        self.assertEqual(wizard.partner_id, self.default_partner)
        self.assertFalse(wizard.partner_matched)
        self.assertEqual(wizard.partner_raw, 'Cong ty khong ton tai')
        self.assertEqual(wizard.unmatched_count, 2)

    def test_schema_refusal_retries_without_schema(self):
        def refuse_schema(call, text_schema):
            if text_schema:
                raise UserError('Invalid parameter: response_format json_schema is not supported')

        wizard = self._wizard(template_id=self.sale_template.id)
        reply = '```json\n' + json.dumps(self._sale_reply()) + '\n```'
        calls = self._extract(wizard, reply, side_effect=refuse_schema)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[1]['text_schema'])
        self.assertEqual(wizard.state, 'review')

    def test_other_provider_errors_are_not_retried(self):
        def fail(call, text_schema):
            raise UserError('Incorrect API key provided')

        wizard = self._wizard(template_id=self.sale_template.id)
        with self.assertRaisesRegex(UserError, 'API key'):
            self._extract(wizard, '{}', side_effect=fail)

    def test_unreadable_reply_raises(self):
        wizard = self._wizard(template_id=self.sale_template.id)
        with self.assertRaisesRegex(UserError, 'readable order data'):
            self._extract(wizard, 'I cannot read this document.')

    def test_text_mode_without_text_layer_raises(self):
        self.sale_template.input_mode = 'text'
        wizard = self._wizard(template_id=self.sale_template.id)
        with self.assertRaisesRegex(UserError, 'no text layer'):
            self._extract(wizard, self._sale_reply())

    def test_rejects_non_document_upload(self):
        wizard = self._wizard(template_id=self.sale_template.id,
                              file=base64.b64encode(b'just some text'), filename='a.txt')
        with self.assertRaisesRegex(UserError, 'Upload a PDF'):
            self._extract(wizard, self._sale_reply())

    def test_missing_model_or_key(self):
        self.env.company.ocr_model_id = False
        wizard = self._wizard(template_id=self.sale_template.id)
        with self.assertRaisesRegex(UserError, 'No AI model'):
            self._extract(wizard, self._sale_reply())
        self.sale_template.model_id = self.ai_model
        self.ai_model.provider_id.sudo().api_key = False
        with self.assertRaisesRegex(UserError, 'not configured'):
            self._extract(wizard, self._sale_reply())

    # ----------------------------------------------------------
    # Orders
    # ----------------------------------------------------------

    def test_create_sale_order_keeps_sections_and_notes(self):
        wizard = self._wizard(template_id=self.sale_template.id)
        self._extract(wizard, self._sale_reply())
        action = wizard.action_create_order()
        order = self.env['sale.order'].browse(action['res_id'])

        self.assertEqual(action['res_model'], 'sale.order')
        self.assertEqual(order.partner_id, self.customer)
        self.assertEqual(order.client_order_ref, 'PO-2026-001')
        self.assertEqual(str(order.commitment_date.date()), '2026-11-05')
        self.assertIn('Thanh toan 30 ngay.', order.note)
        self.assertEqual(
            [(line.display_type or 'product', line.name) for line in order.order_line],
            [('line_section', 'Van phong pham'), ('product', 'But bi xanh'),
             ('product', 'Giay A4 70gsm'), ('line_note', 'Hang giao 2 dot'), ('product', 'Hop dung but')],
        )
        pen = order.order_line[1]
        self.assertEqual((pen.product_uom_qty, pen.price_unit, pen.discount), (10, 4500, 5))
        self.assertEqual(pen.tax_ids.mapped('amount'), [8.0])
        self.assertEqual(order.order_line[4].product_id, self.default_product)
        attachment = wizard.attachment_id
        self.assertEqual((attachment.res_model, attachment.res_id), ('sale.order', order.id))
        self.assertIn(attachment, order.message_ids.attachment_ids)

    def test_create_purchase_order_matches_vendor_code(self):
        reply = {
            'partner_name': 'Nha cung cap XYZ', 'partner_vat': None, 'reference': 'BG-77',
            'order_date': '2026-10-21', 'delivery_date': '2026-10-30', 'currency': 'USD',
            'note': None,
            'lines': [
                {'type': 'section', 'description': 'Vat tu in', 'code': None, 'quantity': None,
                 'uom': None, 'unit_price': None, 'discount': None, 'tax_percent': None},
                {'type': 'product', 'code': 'XYZ-INK', 'description': 'Ink cartridge', 'quantity': 4,
                 'uom': None, 'unit_price': 65.5, 'discount': None, 'tax_percent': None},
            ],
        }
        wizard = self._wizard('purchase', template_id=self.purchase_template.id)
        self._extract(wizard, reply)
        self.assertEqual(wizard.partner_id, self.vendor)
        self.assertEqual(wizard.line_ids[1].product_id, self.ink)

        order = self.env['purchase.order'].browse(wizard.action_create_order()['res_id'])
        self.assertEqual(order.partner_ref, 'BG-77')
        self.assertEqual(order.currency_id.name, 'USD')
        self.assertEqual([line.display_type for line in order.order_line], ['line_section', False])
        ink = order.order_line[1]
        self.assertEqual((ink.product_id, ink.product_qty, ink.price_unit), (self.ink, 4, 65.5))
        self.assertEqual(str(ink.date_planned.date()), '2026-10-30')

    def test_create_requires_partner_and_products(self):
        self.sale_template.default_partner_id = False
        reply = {**self._sale_reply(), 'partner_name': 'Nobody', 'partner_vat': None}
        wizard = self._wizard(template_id=self.sale_template.id)
        self._extract(wizard, reply)
        with self.assertRaisesRegex(UserError, 'Pick a partner'):
            wizard.action_create_order()
        wizard.partner_id = self.customer
        wizard.line_ids.filtered(lambda l: not l.display_type)[:1].product_id = False
        with self.assertRaisesRegex(UserError, 'Pick a product'):
            wizard.action_create_order()
