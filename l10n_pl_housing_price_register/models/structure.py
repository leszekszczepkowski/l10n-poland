# Copyright 2026 Leszek Szczepkowski
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Daily price file for the Polish housing price register (dane.gov.pl).

Pure functions, no ORM, so the format can be unit tested on its own.

Legal basis: art. 19b of the Act of 20 May 2021 on the protection of the rights of
the purchaser of a residential unit or a single-family house and the Developer
Guarantee Fund (Dz.U. 2026 poz. 1077, in force since 11 November 2026).

Columns: draft regulation of the Minister of Digital Affairs of 23 September 2026
on the structure of the data (legislacja.rcl.gov.pl, project 12415103), annex table:
57 fields in groups A-J. Column order follows the table, the first row holds the
field names, "X" marks a field that does not apply, amounts have two decimals,
dates are YYYY-MM-DD.

Delivery: dane.gov.pl harvester XML (namespace urn:otwarte-dane:harvester:1.13,
COI instructions v1.0.6). Every day is a separate resource that links to that
day's CSV file; the XML keeps growing.

The column names are legal text and stay in Polish on purpose.
"""

import csv
import hashlib
import io
import re
import uuid
from xml.sax.saxutils import escape

STRUCTURE_VERSION = "R-2026-09-23"

# (key, symbol, field name from the annex - verbatim, type t/n/d, required)
COLUMNS = [
    ("company_name", "A1", "Firma", "t", True),
    ("legal_form", "A2", "Forma prawna", "t", True),
    ("krs", "A3", "Numer w Krajowym Rejestrze Sądowym (KRS)", "t", False),
    (
        "ceidg",
        "A4",
        "Numer wpisu do Centralnej Ewidencji i Informacji o Działalności "
        "Gospodarczej (CEIDG)",
        "t",
        False,
    ),
    ("nip", "A5", "Numer Identyfikacji Podatkowej (NIP)", "t", False),
    ("regon", "A6", "Numer identyfikacyjny REGON", "t", False),
    ("phone", "A7", "Numer telefonu", "t", True),
    ("email", "A8", "Adres poczty elektronicznej", "t", True),
    ("website", "A9", "Adres strony internetowej", "t", True),
]
_SEAT = "adresu siedziby albo głównego miejsca wykonywania działalności"
_SALES = "adresu lokalu, w którym jest prowadzona sprzedaż"
_SITE = "lokalizacji przedsięwzięcia deweloperskiego lub zadania inwestycyjnego"
COLUMNS += [
    ("seat_state", "B1", "Województwo " + _SEAT, "t", True),
    ("seat_county", "B2", "Powiat " + _SEAT, "t", True),
    ("seat_municipality", "B3", "Gmina " + _SEAT, "t", True),
    ("seat_city", "B4", "Miejscowość " + _SEAT, "t", True),
    ("seat_street", "B5", "Ulica " + _SEAT, "t", False),
    ("seat_house_no", "B6", "Numer nieruchomości " + _SEAT, "t", True),
    ("seat_flat_no", "B7", "Numer lokalu " + _SEAT, "t", False),
    ("seat_zip", "B8", "Kod pocztowy " + _SEAT, "t", True),
    ("sales_state", "C1", "Województwo " + _SALES, "t", True),
    ("sales_county", "C2", "Powiat " + _SALES, "t", True),
    ("sales_municipality", "C3", "Gmina " + _SALES, "t", True),
    ("sales_city", "C4", "Miejscowość " + _SALES, "t", True),
    ("sales_street", "C5", "Ulica " + _SALES, "t", False),
    ("sales_house_no", "C6", "Numer nieruchomości " + _SALES, "t", True),
    ("sales_flat_no", "C7", "Numer lokalu " + _SALES, "t", False),
    ("sales_zip", "C8", "Kod pocztowy " + _SALES, "t", True),
    (
        "sales_other_locations",
        "C9",
        "Dodatkowe lokalizacje, w których jest prowadzona sprzedaż",
        "t",
        False,
    ),
    (
        "sales_contact_method",
        "C10",
        "Sposób kontaktu nabywcy z podmiotem obowiązanym",
        "t",
        True,
    ),
    ("site_state", "D1", "Województwo " + _SITE, "t", True),
    ("site_county", "D2", "Powiat " + _SITE, "t", True),
    ("site_municipality", "D3", "Gmina " + _SITE, "t", True),
    ("site_city", "D4", "Miejscowość " + _SITE, "t", True),
    ("site_street", "D5", "Ulica " + _SITE, "t", False),
    ("site_house_no", "D6", "Numer nieruchomości " + _SITE, "t", False),
    ("site_zip", "D7", "Kod pocztowy " + _SITE, "t", True),
]
_UNIT = "lokalu mieszkalnego albo domu jednorodzinnego"
_PRODUCT = (
    "Cena lokalu mieszkalnego albo domu jednorodzinnego, będącego przedmiotem "
    "umowy, stanowiąca iloczyn ceny m2 oraz powierzchni użytkowej"
)
_TOTAL = (
    "lokalu mieszkalnego albo domu jednorodzinnego, obejmująca łączną cenę całej "
    "nieruchomości albo jej części będącej przedmiotem umowy oraz cenę i "
    "świadczenia pieniężne, o których mowa w art. 19a ust. 1 pkt 2 i 3 ustawy z "
    "dnia 20 maja 2021 r. o ochronie praw nabywcy lokalu mieszkalnego lub domu "
    "jednorodzinnego oraz Deweloperskim Funduszu Gwarancyjnym"
)
_ANNEX = (
    "o których mowa w art. 2 ust. 4 ustawy z dnia 24 czerwca 1994 r. o własności lokali"
)
_RIGHTS = (
    "praw niezbędnych do korzystania z lokalu mieszkalnego lub domu jednorodzinnego"
)
_OTHER = (
    "innych świadczeń pieniężnych, które nabywca zobowiązany jest spełnić na rzecz "
    "podmiotu obowiązanego w wykonaniu umowy przenoszącej własność"
)
COLUMNS += [
    ("property_type", "E1", "Rodzaj nieruchomości", "t", True),
    (
        "unit_number",
        "E2",
        "Numer " + _UNIT + ", nadany przez podmiot obowiązany",
        "t",
        True,
    ),
    ("price_m2", "E3", "Cena 1 m2 powierzchni użytkowej " + _UNIT, "n", True),
    (
        "price_m2_date",
        "E4",
        "Data, od której obowiązuje cena 1 m2 powierzchni użytkowej " + _UNIT,
        "d",
        True,
    ),
    ("price", "E5", _PRODUCT, "n", True),
    (
        "price_date",
        "E6",
        "Data, od której obowiązuje c" + _PRODUCT[1:],
        "d",
        True,
    ),
    ("total_price", "E7", "Cena " + _TOTAL, "n", False),
    ("total_price_date", "E8", "Data, od której obowiązuje cena " + _TOTAL, "d", False),
    (
        "part_type",
        "F1",
        "Rodzaj części nieruchomości będącej przedmiotem umowy",
        "t",
        False,
    ),
    (
        "part_ref",
        "F2",
        "Oznaczenie części nieruchomości nadane przez podmiot obowiązany",
        "t",
        False,
    ),
    (
        "part_price",
        "F3",
        "Cena części nieruchomości będącej przedmiotem umowy",
        "n",
        False,
    ),
    (
        "part_date",
        "F4",
        "Data, od której obowiązuje cena części nieruchomości, będących przedmiotem "
        "umowy",
        "d",
        False,
    ),
    ("annex_type", "G1", "Rodzaj pomieszczeń przynależnych, " + _ANNEX, "t", False),
    (
        "annex_ref",
        "G2",
        "Oznaczenie pomieszczenia przynależnego, o którym mowa w art. 2 ust. 4 ustawy "
        "z dnia 24 czerwca 1994 r. o własności lokali",
        "t",
        False,
    ),
    (
        "annex_price",
        "G3",
        "Wyszczególnienie cen pomieszczeń przynależnych, " + _ANNEX,
        "n",
        False,
    ),
    (
        "annex_date",
        "G4",
        "Data, od której obowiązuje cena wyszczególnionych pomieszczeń "
        "przynależnych, " + _ANNEX,
        "d",
        False,
    ),
    ("rights_desc", "H1", "Wyszczególnienie " + _RIGHTS, "t", False),
    ("rights_value", "H2", "Wartość " + _RIGHTS, "n", False),
    (
        "rights_date",
        "H3",
        "Data, od której obowiązuje wartości " + _RIGHTS,
        "d",
        False,
    ),
    ("other_desc", "I1", "Wyszczególnienie rodzajów " + _OTHER, "t", False),
    (
        "other_value",
        "I2",
        "Wartość innego świadczenia pieniężnego, które nabywca zobowiązany jest "
        "spełnić na rzecz podmiotu obowiązanego w wykonaniu umowy przenoszącej "
        "własność",
        "n",
        False,
    ),
    (
        "other_date",
        "I3",
        "Data, od której obowiązuje cena wartości " + _OTHER,
        "d",
        False,
    ),
    (
        "prospectus_url",
        "J1",
        "Adres strony internetowej, pod którym jest dostępny prospekt informacyjny",
        "t",
        True,
    ),
]
HEADER = [c[2] for c in COLUMNS]
KEYS = [c[0] for c in COLUMNS]

# E1 values are legal text as well
PROPERTY_TYPES = {
    "flat": "lokal mieszkalny",
    "house": "dom jednorodzinny",
}

# Fixed namespace for resource identifiers - never change it, dane.gov.pl keeps them
NS_UUID = uuid.UUID("5b1f3c2e-8a4d-4f1e-9c7a-2d6e0b9a4c11")
_DIGITS_ONLY = ("phone", "krs", "nip", "regon")


def amount(value):
    """779000 -> '779000.00' (dot, because the column separator is a semicolon)."""
    return f"{round(float(value), 2):.2f}"


def cell(key, ftype, value):
    if value is None or value is False or value == "":
        return "X"
    if ftype == "n":
        return amount(value)
    if ftype == "d":
        return value if isinstance(value, str) else value.strftime("%Y-%m-%d")
    if key in _DIGITS_ONLY:
        return re.sub(r"[^0-9+]", "", str(value))
    return re.sub(r"\s+", " ", str(value)).strip()


def missing(record):
    """Symbols of required fields without a value: such a row must not be sent."""
    return [
        sym
        for (key, sym, _name, _t, req) in COLUMNS
        if req and record.get(key) in (None, False, "")
    ]


def row(record):
    return [cell(key, t, record.get(key)) for (key, _s, _n, t, _r) in COLUMNS]


def csv_bytes(records):
    """UTF-8 without BOM, semicolon, CRLF - like the ministry sample file."""
    buf = io.StringIO()
    writer = csv.writer(
        buf, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL
    )
    writer.writerow(HEADER)
    for rec in records:
        writer.writerow(row(rec))
    return buf.getvalue().encode("utf-8")


def slugify(text):
    table = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").translate(table).lower()).strip("-")
    return slug or "x"


def file_name(developer_slug, project_slug, day):
    return (
        f"ceny-ofertowe-mieszkan-dewelopera-{developer_slug}-{project_slug}-{day}.csv"
    )


def resource_ext_ident(dataset_ext_ident, project_slug, day):
    """Stable 36-character resource id (UUID5): the same day gives the same id."""
    return str(uuid.uuid5(NS_UUID, f"{dataset_ext_ident}/{project_slug}/{day}"))


def md5(data):
    return hashlib.md5(data).hexdigest()  # noqa: S324 - checksum required by dane.gov.pl


def _tag(tag, text):
    return f"<{tag}>{escape(str(text))}</{tag}>"


def _pl_en(tag, text_pl, text_en):
    limit = 300 if tag == "title" else 1000
    return (
        f"<{tag}>{_tag('polish', text_pl[:limit])}"
        f"{_tag('english', text_en[:limit])}</{tag}>"
    )


LEGAL_BASIS_PL = (
    "zgodnie z art. 19b ust. 1 ustawy z dnia 20 maja 2021 r. o ochronie praw "
    "nabywcy lokalu mieszkalnego lub domu jednorodzinnego oraz Deweloperskim "
    "Funduszu Gwarancyjnym"
)
LEGAL_BASIS_EN = (
    "in accordance with art. 19b ust. 1 of the Act of 20 May 2021 on the protection "
    "of the rights of the purchaser of a residential unit or a single-family house "
    "and the Developer Guarantee Fund"
)
FLAGS = (
    "<hasDynamicData>false</hasDynamicData>"
    "<hasHighValueData>true</hasHighValueData>"
    "<hasHighValueDataFromEuropeanCommissionList>false"
    "</hasHighValueDataFromEuropeanCommissionList>"
    "<hasResearchData>false</hasResearchData>"
)


def harvester_xml(dataset, resources):
    """Build the harvester XML.

    dataset: dict(ext_ident, company, year, url)
    resources: list of dict(ext_ident, url, day, project)
    """
    company = dataset["company"]
    out = [
        "<?xml version='1.0' encoding='UTF-8'?>",
        '<ns2:datasets xmlns:ns2="urn:otwarte-dane:harvester:1.13" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">',
        '<dataset status="published">',
        _tag("extIdent", dataset["ext_ident"]),
        _pl_en(
            "title",
            f"Ceny ofertowe mieszkań dewelopera {company} w {dataset['year']} r.",
            f"Offer prices of apartments of developer {company} in {dataset['year']}.",
        ),
        _pl_en(
            "description",
            "Zbiór danych zawiera informacje o cenach ofertowych mieszkań "
            f"dewelopera {company} udostępniane {LEGAL_BASIS_PL}.",
            "The dataset contains information on offer prices of apartments of "
            f"the developer {company} made available {LEGAL_BASIS_EN}.",
        ),
    ]
    if dataset.get("url"):
        out.append(_tag("url", dataset["url"]))
    out += [
        "<updateFrequency>daily</updateFrequency>",
        FLAGS,
        "<categories><category>ECON</category></categories>",
        "<resources>",
    ]
    for res in resources:
        name = company + (f" — {res['project']}" if res.get("project") else "")
        day = res["day"]
        out += [
            '<resource status="published">',
            _tag("extIdent", res["ext_ident"]),
            _tag("url", res["url"]),
            _pl_en(
                "title",
                f"Ceny ofertowe mieszkań dewelopera {name} {day}",
                f"Offer prices for developer's apartments {name} {day}",
            ),
            _pl_en(
                "description",
                f"Dane dotyczące cen ofertowych mieszkań dewelopera {name} "
                f"udostępnione {day} {LEGAL_BASIS_PL}.",
                f"Data on offer prices of apartments of the developer {name} "
                f"made available {day} {LEGAL_BASIS_EN}.",
            ),
            "<availability>local</availability>",
            _tag("dataDate", day),
            "<specialSigns><specialSign>X</specialSign></specialSigns>",
            FLAGS,
            "<containsProtectedData>false</containsProtectedData>",
            "</resource>",
        ]
    out += [
        "</resources>",
        '<tags><tag lang="pl">Deweloper</tag></tags>',
        "</dataset>",
        "</ns2:datasets>",
    ]
    return ("\n".join(out) + "\n").encode("utf-8")
