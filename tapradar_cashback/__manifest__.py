{
    "name": "Cashback Gift Cards",
    "summary": "Every POS purchase during a campaign automatically earns a personal gift card "
    "(percentage or fixed amount) for the next visit, usable only after the campaign",
    "version": "17.0.1.0.0",
    "category": "Sales/Point of Sale",
    "author": "TapRadar",
    "website": "https://tapradar.app",
    "license": "LGPL-3",
    "depends": ["pos_loyalty"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "wizard/campaign_preview_views.xml",
        "views/campaign_views.xml",
        "views/tapradar_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "tapradar_cashback/static/src/**/*",
        ],
    },
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": False,
}
