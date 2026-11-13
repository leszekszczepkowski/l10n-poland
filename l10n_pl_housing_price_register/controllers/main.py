# Copyright 2026 Leszek Szczepkowski
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Public addresses for dane.gov.pl: no website module, fixed URL, no login.

/rejestr-cen/<slug>/dane.xml                   harvester XML (all daily files)
/rejestr-cen/<slug>/dane.md5                   MD5 of that XML
/rejestr-cen/<slug>/<file>.csv                 stored daily file (never changed)
/rejestr-cen/<slug>/najnowszy-<project>.csv    latest daily file of a project
                                               (fixed address for a resource
                                               added by hand in the dane.gov.pl panel)
/rejestr-cen/preview/<id>.csv                  today's file preview, logged in users

Only the content of the daily files leaves Odoo: the fields of the regulation,
no buyer data.
"""

import logging
import re

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

from ..models import structure
from ..models.hpr_approval_mixin import USER_GROUP, today_pl
from ..models.hpr_developer import SLUG_RE

_logger = logging.getLogger(__name__)


class HousingPriceRegisterController(http.Controller):
    def _developer(self, slug):
        if not re.fullmatch(SLUG_RE, slug or ""):
            return None
        # sudo: public route; only file metadata and daily file content leave Odoo.
        # Archived developers are served too: dane.gov.pl keeps their addresses.
        return (
            request.env["l10n_pl.hpr.developer"]
            .sudo()
            .with_context(active_test=False)
            .search([("slug", "=", slug)], limit=1)
            or None
        )

    @staticmethod
    def _response(data, content_type, filename=None):
        etag = structure.md5(data)
        if request.httprequest.if_none_match.contains(etag):
            return request.make_response(
                b"", headers=[("ETag", f'"{etag}"')], status=304
            )
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(data))),
            ("Cache-Control", "no-cache"),
            ("ETag", f'"{etag}"'),
            ("X-Content-Type-Options", "nosniff"),
        ]
        if filename:
            headers.append(("Content-Disposition", f'inline; filename="{filename}"'))
        return request.make_response(data, headers=headers)

    @http.route(
        "/rejestr-cen/<string:slug>/dane.xml",
        type="http",
        auth="public",
        methods=["GET", "HEAD"],
        csrf=False,
        sitemap=False,
    )
    def harvester_xml(self, slug, **kw):
        developer = self._developer(slug)
        xml = developer and developer._hpr_xml()
        if not xml:
            raise request.not_found()
        return self._response(xml, "application/xml; charset=utf-8")

    @http.route(
        "/rejestr-cen/<string:slug>/dane.md5",
        type="http",
        auth="public",
        methods=["GET", "HEAD"],
        csrf=False,
        sitemap=False,
    )
    def harvester_md5(self, slug, **kw):
        developer = self._developer(slug)
        xml = developer and developer._hpr_xml()
        if not xml:
            raise request.not_found()
        return self._response(structure.md5(xml).encode(), "text/plain; charset=utf-8")

    @http.route(
        "/rejestr-cen/<string:slug>/<string:filename>",
        type="http",
        auth="public",
        methods=["GET", "HEAD"],
        csrf=False,
        sitemap=False,
    )
    def daily_file(self, slug, filename, **kw):
        developer = self._developer(slug)
        if not developer or not re.fullmatch(r"[a-z0-9-]+\.csv", filename or ""):
            raise request.not_found()
        # sudo: public route, files are public by law
        File = request.env["l10n_pl.hpr.file"].sudo()
        latest = re.fullmatch(r"najnowszy-([a-z0-9-]+)\.csv", filename)
        if latest:
            found = File.search(
                [
                    ("developer_id", "=", developer.id),
                    ("project_id.slug", "=", latest.group(1)),
                ],
                limit=1,
            )
        else:
            found = File.search(
                [("developer_id", "=", developer.id), ("name", "=", filename)], limit=1
            )
        if not found:
            raise request.not_found()
        raw = found.attachment_id.raw or b""
        if structure.md5(raw) != found.md5:
            # content never changes; a mismatch is an alarm, do not serve it
            _logger.error("Housing price register: MD5 of %s does not match", filename)
            return request.make_response("Inconsistent file", status=500)
        return self._response(raw, "text/csv; charset=utf-8", found.name)

    @http.route(
        "/rejestr-cen/preview/<int:project_id>.csv",
        type="http",
        auth="user",
        methods=["GET"],
        sitemap=False,
    )
    def preview(self, project_id, **kw):
        if not request.env.user.has_group(USER_GROUP):
            raise request.not_found()
        project = request.env["l10n_pl.hpr.project"].browse(project_id).exists()
        if not project:
            raise request.not_found()
        try:
            project.check_access("read")  # company rules of the user
        except AccessError:
            raise request.not_found() from None
        records, _skipped = project._hpr_units_for_file(today_pl())
        return self._response(
            structure.csv_bytes(records),
            "text/csv; charset=utf-8",
            f"preview-{project.slug}.csv",
        )
