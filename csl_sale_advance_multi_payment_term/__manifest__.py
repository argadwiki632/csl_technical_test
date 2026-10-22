{
    "name": "CSL Sale Advance Multi Payment Term",
    "summary": "Invoice confirmed sales orders per approved Term of Payment (TOP)",
    "version": "18.0.1.1.0",
    "category": "Sales/Sales",
    "author": "Arga Dwiki Suwandi",
    "license": "LGPL-3",
    "depends": ["sale_management"],
    "data": [
        "security/sale_order_top_security.xml",
        "security/ir.model.access.csv",
        "wizard/sale_order_top_reject_views.xml",
        "views/sale_order_top_views.xml",
        "views/sale_order_views.xml",
        "views/account_move_views.xml",
    ],
    "installable": True,
}
