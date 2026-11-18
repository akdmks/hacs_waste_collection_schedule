"""REMONDIS terminarz wywozu (harmonogramy.remondis.pl), e.g. Bydgoszcz.

Composes: :class:`~waste_collection_schedule.retrievers.LookupChainRetriever`.
The search form (voivodeship, city, street and house number) answers a GET
with a table of matching properties; each row links to a printable schedule
for that property. The lookup picks the row for the configured address and
hands the link on as the schedule URL. The schedule is a table with a row per
month and a column per waste type, each cell listing the collection days of
that month in the current year.
"""

import datetime
import re
from typing import ClassVar, final
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from waste_collection_schedule import parsers, retrievers
from waste_collection_schedule import waste_types as wt
from waste_collection_schedule.base_source import BaseSource
from waste_collection_schedule.config_params import (
    city,
    dropdown,
    house_number,
    street,
)
from waste_collection_schedule.exceptions import (
    SourceArgAmbiguousWithSuggestions,
    SourceArgumentNotFound,
    SourceArgumentNotFoundWithSuggestions,
)
from waste_collection_schedule.transformers import ICSTransformer

_BASE = "https://harmonogramy.remondis.pl"

# The ``wojewodztwo`` option values of the search form.
_VOIVODESHIPS = {
    "dolnośląskie": 12,
    "kujawsko-pomorskie": 13,
    "lubelskie": 14,
    "lubuskie": 15,
    "łódzkie": 16,
    "małopolskie": 2,
    "mazowieckie": 1,
    "opolskie": 3,
    "podkarpackie": 4,
    "podlaskie": 5,
    "pomorskie": 6,
    "śląskie": 7,
    "świętokrzyskie": 8,
    "warmińsko-mazurskie": 9,
    "wielkopolskie": 10,
    "zachodniopomorskie": 11,
}

_MONTHS = {
    "STYCZEN": 1,
    "LUTY": 2,
    "MARZEC": 3,
    "KWIECIEN": 4,
    "MAJ": 5,
    "CZERWIEC": 6,
    "LIPIEC": 7,
    "SIERPIEN": 8,
    "WRZESIEN": 9,
    "PAZDZIERNIK": 10,
    "LISTOPAD": 11,
    "GRUDZIEN": 12,
}
_ACCENTS = str.maketrans("ĄĆĘŁŃÓŚŹŻąćęłńóśźż", "ACELNOSZZacelnoszz")
_YEAR_RE = re.compile(r"w poszczególnych miesiącach (\d{4}) roku")

_TYPE_MAP = {
    "Zmieszane odpady komunalne": wt.GENERAL_WASTE,
    "Papier": wt.PAPER,
    "Szkło": wt.GLASS,
    "Plastik, metale": wt.RECYCLABLES,
    "Odpady biodegradowalne": wt.ORGANIC,
    "Odpady wielkogabarytowe": wt.BULKY_WASTE,
}


def _norm(value: str) -> str:
    return " ".join(str(value).translate(_ACCENTS).upper().split())


def _pick_schedule_url(response, *_keys, city, street, house_number=None, **_):
    """The printable-schedule link of the search row for the configured address.

    A row matches when its city equals the configured city and its street cell
    is the configured ``street number``, optionally followed by a suffix the
    provider appends (a company or estate name, e.g. "KASZTANOWA 32 POLSKI
    TYTOŃ"). Without a house number every row of the street matches, so the
    reply lists the numbers to choose from.
    """
    wanted_city = _norm(city)
    wanted = _norm(f"{street} {house_number or ''}")
    soup = BeautifulSoup(response.text, "html.parser")

    rows = []  # (street label, schedule link) of every row in the wanted city
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        link = row.find("a", href=re.compile(r"/Home/Wydruk"))
        if len(cells) < 2 or link is None:
            continue
        if _norm(cells[1].get_text(" ", strip=True)) != wanted_city:
            continue
        label = " ".join(cells[0].get_text(" ", strip=True).split())
        rows.append((label, urljoin(f"{_BASE}/", link["href"])))

    if not rows:
        raise SourceArgumentNotFound("city/street", f"{city}, {wanted.title()}")

    exact = [row for row in rows if _norm(row[0]) == wanted]
    matches = exact or [row for row in rows if _norm(row[0]).startswith(f"{wanted} ")]
    if len(matches) == 1:
        return matches[0][1]
    labels = sorted({label for label, _ in (matches or rows)})
    if matches:
        raise SourceArgAmbiguousWithSuggestions("street", wanted.title(), labels)
    raise SourceArgumentNotFoundWithSuggestions("street", wanted.title(), labels)


def _month_rows(tables, source=None):
    """``(date, label)`` rows of the schedule table."""
    for table in tables:
        year_match = _YEAR_RE.search(table.get_text(" "))
        header = table.find("thead")
        if year_match is None or header is None:
            raise ValueError("REMONDIS schedule layout changed")
        year = int(year_match.group(1))
        labels = [th.get_text(strip=True) for th in header.find_all("tr")[-1]("th")]
        for row in table.find("tbody").find_all("tr"):
            cells = [td.get_text(strip=True) for td in row("td")]
            month = _MONTHS.get(_norm(cells[0]))
            if month is None:
                raise ValueError(f"unknown month {cells[0]!r}")
            for label, cell in zip(labels[1:], cells[1:], strict=True):
                for day in re.findall(r"\d+", cell):
                    yield datetime.date(year, month, int(day)), label


@final
class Source(BaseSource):
    TITLE = "REMONDIS (harmonogramy.remondis.pl)"
    DESCRIPTION = (
        "Source for REMONDIS waste collection schedules (e.g. Bydgoszcz) "
        "published on harmonogramy.remondis.pl."
    )
    URL = "https://harmonogramy.remondis.pl"
    COUNTRY = "pl"
    SOURCE_CODEOWNERS: ClassVar[list] = ["@akdmks"]
    RAISE_ON_EMPTY = True
    WASTE_TYPES: ClassVar[list] = [
        wt.GENERAL_WASTE,
        wt.PAPER,
        wt.GLASS,
        wt.RECYCLABLES,
        wt.ORGANIC,
        wt.BULKY_WASTE,
    ]

    TEST_CASES: ClassVar[dict] = {
        "Bydgoszcz, Kasztanowa 32": {
            "voivodeship": "kujawsko-pomorskie",
            "city": "Bydgoszcz",
            "street": "Kasztanowa",
            "house_number": "32",
        },
    }

    PARAMS = (
        dropdown("voivodeship", list(_VOIVODESHIPS), "Voivodeship"),
        city(),
        street(),
        house_number(optional=True),
    )

    HOWTO: ClassVar[dict] = {
        "en": (
            "Open https://harmonogramy.remondis.pl/, choose your voivodeship "
            "and enter the city, street and house number exactly as you would "
            "in the search form. If the house number is left out, the error "
            "message lists the matching addresses of the street."
        ),
    }

    retrieve = retrievers.LookupChainRetriever(
        steps=(
            retrievers.Lookup(
                f"{_BASE}/Home/Szukaj",
                params=lambda voivodeship, city, street, house_number=None, **_: {
                    "wojewodztwo": _VOIVODESHIPS[voivodeship],
                    "miasto": city,
                    "ulica": f"{street} {house_number or ''}".strip(),
                },
                pick=_pick_schedule_url,
                encoding="utf-8",
            ),
        ),
        url=lambda *keys, **_: keys[-1],
        encoding="utf-8",
    )
    parse = parsers.HtmlParser("table", require=["thead", "tbody"])
    preprocess = staticmethod(_month_rows)
    transform = ICSTransformer(type_value_map=_TYPE_MAP)
