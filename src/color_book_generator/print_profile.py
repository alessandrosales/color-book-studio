"""Print profiles: where the book is printed decides margins, gutter and page multiples.

Values from the discovery (``docs/discovery/padroes-editoriais-e-contexto.md``):
- KDP paperback: inside margin grows with page count (0.375" up to 150 pages ...
  0.875" up to 828), outside >= 0.25" without bleed / 0.375" with bleed, minimum 24
  pages, even page count, bleed 0.125".
- Brazilian print shops: 3 mm bleed, 3-5 mm safety margin, perfect binding in signatures
  (multiples of 4 digitally, 8/16 in offset), saddle stitch in multiples of 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MM_PER_INCH = 25.4


def mm(value: float) -> float:
    return value / MM_PER_INCH


@dataclass(frozen=True)
class PrintProfile:
    id: str
    name: str
    description: str
    # (max_pages, inside margin in inches), ascending by max_pages.
    gutter_table: tuple[tuple[int, float], ...]
    outside_in: float
    outside_with_bleed_in: float
    top_bottom_in: float
    bleed_in: float
    page_multiple: int
    min_pages: int
    max_pages: int
    spine_per_page_in: float = 0.0
    spine_text_min_pages: int = 0
    supported_sizes: frozenset[str] = field(default_factory=frozenset)
    notes: tuple[str, ...] = ()

    def gutter_in(self, pages: int) -> float:
        for max_pages, inside in self.gutter_table:
            if pages <= max_pages:
                return inside
        return self.gutter_table[-1][1]

    def outside(self, has_bleed: bool) -> float:
        return self.outside_with_bleed_in if has_bleed else self.outside_in

    def padded_pages(self, pages: int) -> int:
        """Page count after padding with blank pages to the profile's multiple."""
        multiple = max(1, self.page_multiple)
        return pages + (-pages) % multiple

    def spine_in(self, pages: int) -> float:
        return round(pages * self.spine_per_page_in, 4)


KDP_SIZES = frozenset(
    {
        "letter_8.5x11",
        "a4",
        "6x9",
        "8x10",
        "5x8",
        "square_8.5x8.5",
        "square_8.25x8.25",
    }
)

PRINT_PROFILES: dict[str, PrintProfile] = {
    "kdp_paperback": PrintProfile(
        id="kdp_paperback",
        name="Amazon KDP (brochura)",
        description="Gutter cresce com o número de páginas; mínimo de 24 páginas, total par.",
        gutter_table=((150, 0.375), (300, 0.5), (500, 0.625), (700, 0.75), (828, 0.875)),
        outside_in=0.25,
        outside_with_bleed_in=0.375,
        top_bottom_in=0.25,
        bleed_in=0.125,
        page_multiple=2,
        min_pages=24,
        max_pages=828,
        spine_per_page_in=0.002252,
        spine_text_min_pages=79,
        supported_sizes=KDP_SIZES,
        notes=("Confirme gutter e lombada na calculadora oficial da KDP antes do envio.",),
    ),
    "grafica_brochura": PrintProfile(
        id="grafica_brochura",
        name="Gráfica: brochura (lombada colada)",
        description="Sangria de 3 mm, segurança de 5 mm, páginas em múltiplos de 4.",
        gutter_table=((96, mm(10)), (200, mm(13)), (400, mm(16))),
        outside_in=mm(5),
        outside_with_bleed_in=mm(5),
        top_bottom_in=mm(5),
        bleed_in=mm(3),
        page_multiple=4,
        min_pages=16,
        max_pages=600,
        notes=("Em offset a gráfica pode exigir cadernos de 8 ou 16 páginas; confirme.",),
    ),
    "grafica_canoa": PrintProfile(
        id="grafica_canoa",
        name="Gráfica: grampo (canoa)",
        description="Revista ou gibi grampeado: páginas em múltiplos de 4, até cerca de 64.",
        gutter_table=((64, mm(6)),),
        outside_in=mm(5),
        outside_with_bleed_in=mm(5),
        top_bottom_in=mm(5),
        bleed_in=mm(3),
        page_multiple=4,
        min_pages=8,
        max_pages=64,
        notes=("Acima de ~48 páginas as folhas do meio avançam (creep); peça a compensação à gráfica.",),
    ),
    "home_print": PrintProfile(
        id="home_print",
        name="Impressão em casa / espiral",
        description="Sem sangria e sem múltiplos; margem de 6 mm para a impressora.",
        gutter_table=((1000, mm(6)),),
        outside_in=mm(6),
        outside_with_bleed_in=mm(6),
        top_bottom_in=mm(6),
        bleed_in=0.0,
        page_multiple=1,
        min_pages=1,
        max_pages=1000,
    ),
}

DEFAULT_PROFILE = "kdp_paperback"


def get_profile(profile_id: str | None) -> PrintProfile:
    return PRINT_PROFILES.get(profile_id or DEFAULT_PROFILE, PRINT_PROFILES[DEFAULT_PROFILE])


@dataclass(frozen=True)
class Margins:
    """Required clear zone around the live area, in inches, for one page side."""

    inside: float
    outside: float
    top: float
    bottom: float


def page_margins(profile: PrintProfile, total_pages: int, has_bleed: bool) -> Margins:
    edge = profile.outside(has_bleed)
    return Margins(
        inside=max(profile.gutter_in(total_pages), edge),
        outside=edge,
        top=max(profile.top_bottom_in, edge),
        bottom=max(profile.top_bottom_in, edge),
    )


def is_recto(page_number: int, rtl: bool = False) -> bool:
    """Right-hand page? Page 1 is a recto in a left-to-right book, a verso in a right-to-left one."""
    odd = page_number % 2 == 1
    return odd != rtl


@dataclass(frozen=True)
class Placement:
    """How to draw a full-page image so its content clears the margins (inches, top-left origin)."""

    x: float
    y: float
    scale: float

    @property
    def changed(self) -> bool:
        return abs(self.x) > 1e-3 or abs(self.y) > 1e-3 or abs(self.scale - 1) > 1e-3


IDENTITY = Placement(0.0, 0.0, 1.0)


def _clamp_axis(start: float, size: float, lo: float, hi: float) -> float:
    """New start for a span of ``size`` beginning at ``start``, moved as little as possible into [lo, hi]."""
    if size >= hi - lo:
        return lo + (hi - lo - size) / 2
    if start < lo:
        return lo
    if start + size > hi:
        return hi - size
    return start


def place_content(
    bbox: tuple[float, float, float, float] | None,
    page_w: float,
    page_h: float,
    margins: Margins,
    *,
    recto: bool,
) -> Placement:
    """Smallest move (then uniform shrink) that brings ``bbox`` inside the live area.

    ``bbox`` is (x0, y0, x1, y1) in inches from the page's top-left corner. On a recto the
    spine is on the left, so the inside margin is the left one.
    """
    if bbox is None:
        return IDENTITY
    left = margins.inside if recto else margins.outside
    right = margins.outside if recto else margins.inside
    live_x0, live_x1 = left, page_w - right
    live_y0, live_y1 = margins.top, page_h - margins.bottom
    x0, y0, x1, y1 = bbox
    cw, ch = max(1e-6, x1 - x0), max(1e-6, y1 - y0)
    scale = min(1.0, (live_x1 - live_x0) / cw, (live_y1 - live_y0) / ch)
    if scale < 1.0:
        # Shrink about the content's centre before clamping.
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        x0, y0 = cx - cw * scale / 2, cy - ch * scale / 2
        cw, ch = cw * scale, ch * scale
    new_x0 = _clamp_axis(x0, cw, live_x0, live_x1)
    new_y0 = _clamp_axis(y0, ch, live_y0, live_y1)
    # Image origin so that content origin lands on new_x0/new_y0 after scaling.
    ox = new_x0 - bbox[0] * scale
    oy = new_y0 - bbox[1] * scale
    return Placement(ox, oy, scale)
