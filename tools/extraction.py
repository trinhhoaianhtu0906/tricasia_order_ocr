"""Prompt, JSON schema and response parsing for order extraction."""

import json
import re

NULLABLE_STRING = {'type': ['string', 'null']}
NULLABLE_NUMBER = {'type': ['number', 'null']}

LINE_SCHEMA = {
    'type': 'object',
    'properties': {
        'type': {'type': 'string', 'enum': ['product', 'section', 'note']},
        'code': NULLABLE_STRING,
        'description': {'type': 'string'},
        'quantity': NULLABLE_NUMBER,
        'uom': NULLABLE_STRING,
        'unit_price': NULLABLE_NUMBER,
        'discount': NULLABLE_NUMBER,
        'tax_percent': NULLABLE_NUMBER,
    },
    'required': [
        'type', 'code', 'description', 'quantity', 'uom',
        'unit_price', 'discount', 'tax_percent',
    ],
    'additionalProperties': False,
}

# Every key is required and nullable instead of optional: strict structured
# output (OpenAI and the vendors that copy it) rejects optional properties.
EXTRACTION_SCHEMA = {
    'name': 'order_extraction',
    'schema': {
        'type': 'object',
        'properties': {
            'partner_name': NULLABLE_STRING,
            'partner_vat': NULLABLE_STRING,
            'reference': NULLABLE_STRING,
            'order_date': NULLABLE_STRING,
            'delivery_date': NULLABLE_STRING,
            'currency': NULLABLE_STRING,
            'note': NULLABLE_STRING,
            'lines': {'type': 'array', 'items': LINE_SCHEMA},
        },
        'required': [
            'partner_name', 'partner_vat', 'reference', 'order_date',
            'delivery_date', 'currency', 'note', 'lines',
        ],
        'additionalProperties': False,
    },
}

PARTNER_ROLE = {
    'sale': 'the customer who places the order (not the seller who receives it)',
    'purchase': 'the vendor who sells the goods (not the buyer)',
}

SYSTEM_PROMPT = """You extract order data from business documents (purchase orders, \
quotations, invoices, delivery notes) for an ERP.

Return one JSON object with exactly these keys:
- partner_name: name of {partner_role}.
- partner_vat: that partner's tax ID / VAT / MST, digits and dashes only.
- reference: the document number (PO number, quotation number...).
- order_date, delivery_date: dates as YYYY-MM-DD.
- currency: ISO 4217 code such as VND, USD, EUR.
- note: payment terms, delivery terms or remarks printed outside the line table.
- lines: every row of the item table, in document order. Use type "section" for \
a heading row that groups the items below it (a category or chapter title), \
type "note" for a free-text row without quantity or price, and type "product" for \
an item row. For section and note rows put the text in description and null elsewhere.
  For product rows: code is the item code / SKU as printed, description the item \
name, quantity and unit_price as plain numbers (no thousands separators, the \
decimal separator as a dot), uom the unit as printed, discount in percent, \
tax_percent the VAT rate in percent.

Use null for anything the document does not show. Never invent values. \
Reply with the JSON object only."""


def build_inputs(order_type, instructions, document_block):
    """Return the muk_ai input items asking the model to read ``document_block``."""
    system = SYSTEM_PROMPT.format(partner_role=PARTNER_ROLE[order_type])
    if instructions:
        system += f'\n\nInstructions for this document layout:\n{instructions}'
    return [
        {'role': 'system', 'content': [{'type': 'input_text', 'text': system}]},
        {
            'role': 'user',
            'content': [
                {'type': 'input_text', 'text': 'Extract the order from this document.'},
                document_block,
            ],
        },
    ]


def parse_extraction(text):
    """Decode the model reply into the extraction dict.

    A model answering without structured output may wrap the JSON in a code
    fence or a sentence, so the outermost object is cut out first.

    :raise ValueError: when the reply holds no JSON object
    """
    text = (text or '').strip()
    fenced = re.search(r'```(?:json)?\s*(.*?)```', text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find('{'), text.rfind('}')
    data = json.loads(text[start:end + 1]) if 0 <= start < end else None
    if not isinstance(data, dict):
        raise ValueError(text[:500])
    data['lines'] = [line for line in data.get('lines') or [] if isinstance(line, dict)]
    return data
