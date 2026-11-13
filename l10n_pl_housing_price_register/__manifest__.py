# Copyright 2026 Leszek Szczepkowski
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    "name": "Poland - Housing price registers for developers",
    "summary": "Daily file of developer offer prices for the national price "
    "register on dane.gov.pl (art. 19b of the Polish Developer Act)",
    "version": "19.0.1.0.0",
    "category": "Localization",
    "website": "https://github.com/OCA/l10n-poland",
    "author": "Leszek Szczepkowski, Odoo Community Association (OCA)",
    "maintainers": ["leszekszczepkowski"],
    "license": "LGPL-3",
    "development_status": "Alpha",
    "installable": True,
    "application": False,
    "depends": ["mail", "product"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "data/mail_activity_type.xml",
        "views/hpr_developer_views.xml",
        "views/hpr_project_views.xml",
        "views/hpr_stage_views.xml",
        "views/hpr_unit_views.xml",
        "views/menus.xml",
    ],
}
