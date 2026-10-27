{
    'name': 'Tricasia Order OCR',
    'summary': 'Create sales and purchase orders from PDF documents with MuK AI models',
    'description': """
        Reads a customer or vendor PDF with any chat model configured in
        MuK AI, shows the extracted header and lines for review - sections
        and notes included - and creates the sales or purchase order.
        Unmatched partners and products fall back to the template defaults.
    """,
    'version': '19.0.1.0.0',
    'category': 'Sales',
    'license': 'LGPL-3',
    'author': 'Tricasia',
    'depends': [
        'sale_management',
        'purchase',
        'muk_ai',
        'attachment_indexation',
    ],
    'data': [
        'security/ir.model.access.csv',
        'wizard/order_ocr_wizard_views.xml',
        'views/ocr_template_views.xml',
        'views/order_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
