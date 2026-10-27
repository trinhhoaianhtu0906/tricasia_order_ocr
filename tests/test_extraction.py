from odoo.tests.common import BaseCase, tagged

from odoo.addons.tricasia_order_ocr.tools import EXTRACTION_SCHEMA, build_inputs, parse_extraction


@tagged('post_install', '-at_install')
class TestExtractionTools(BaseCase):

    def test_parse_plain_fenced_and_wrapped_json(self):
        for text in (
            '{"reference": "PO-1", "lines": []}',
            '```json\n{"reference": "PO-1", "lines": []}\n```',
            'Here is the order:\n{"reference": "PO-1", "lines": []}\nDone.',
        ):
            self.assertEqual(parse_extraction(text)['reference'], 'PO-1')

    def test_parse_drops_malformed_lines(self):
        data = parse_extraction('{"lines": [{"type": "product"}, "junk", null]}')
        self.assertEqual(data['lines'], [{'type': 'product'}])
        self.assertEqual(parse_extraction('{"partner_name": "A"}')['lines'], [])

    def test_parse_rejects_non_json(self):
        for text in ('', 'Sorry, I cannot read this.', '{not json}'):
            with self.assertRaises(ValueError):
                parse_extraction(text)

    def test_inputs_carry_role_instructions_and_document(self):
        block = {'type': 'input_text', 'text': 'DOC'}
        inputs = build_inputs('purchase', 'Prices include VAT.', block)
        system = inputs[0]['content'][0]['text']
        self.assertIn('the vendor who sells', system)
        self.assertIn('Prices include VAT.', system)
        self.assertIs(inputs[1]['content'][1], block)
        self.assertIn('the customer who places', build_inputs('sale', '', block)[0]['content'][0]['text'])

    def test_schema_is_strict(self):
        schema = EXTRACTION_SCHEMA['schema']
        self.assertEqual(set(schema['required']), set(schema['properties']))
        line = schema['properties']['lines']['items']
        self.assertEqual(set(line['required']), set(line['properties']))
        self.assertFalse(line['additionalProperties'])
