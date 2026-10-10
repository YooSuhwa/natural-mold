"""Extract original HTML table cells without depending on rendering or whitespace."""

from html.parser import HTMLParser
from pathlib import Path


class PlanTables(HTMLParser):
    """Mutable HTML parser accumulator, restricted to one selected section."""

    def __init__(self, section: str) -> None:
        super().__init__(convert_charrefs=True)
        self.section = section
        self.heading = False
        self.heading_text: list[str] = []
        self.selected = False
        self.cell: list[str] | None = None
        self.row: list[str] = []
        self.rows: list[tuple[str, ...]] = []
        self.paragraph: list[str] | None = None
        self.code: list[str] | None = None
        self.paragraph_codes: list[str] = []
        self.intro_codes: tuple[str, ...] = ()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h3":
            self.heading = True
            self.heading_text = []
            self.selected = False
        if self.selected and tag == "tr":
            self.row = []
        if self.selected and tag == "td":
            self.cell = []
        if self.selected and tag == "p":
            self.paragraph = []
            self.paragraph_codes = []
        if tag == "code" and self.paragraph is not None:
            self.code = []

    def handle_data(self, data: str) -> None:
        if self.heading:
            self.heading_text.append(data)
        if self.cell is not None:
            self.cell.append(data)
        if self.paragraph is not None:
            self.paragraph.append(data)
        if self.code is not None:
            self.code.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3":
            self.heading = False
            self.selected = "".join(self.heading_text).startswith(self.section + " ")
        if tag == "td" and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        if tag == "tr" and self.selected and self.row:
            self.rows.append(tuple(self.row))
        if tag == "code" and self.code is not None:
            self.paragraph_codes.append("".join(self.code))
            self.code = None
        if tag == "p" and self.paragraph is not None:
            if "".join(self.paragraph).startswith("신규 테이블은"):
                self.intro_codes = tuple(self.paragraph_codes)
            self.paragraph = None


def source_rows(plan: Path, section: str) -> tuple[tuple[str, ...], ...]:
    """Return normalized source cells, including labels and every semantic column."""
    parser = PlanTables(section)
    parser.feed(plan.read_text())
    return tuple(parser.rows)


def source_new_table_codes(plan: Path) -> tuple[str, ...]:
    """Capture all original introductory code identifiers, including paragraph tables."""
    parser = PlanTables("6.1")
    parser.feed(plan.read_text())
    return parser.intro_codes
