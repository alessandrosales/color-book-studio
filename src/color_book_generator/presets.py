"""Style catalog: six categories with several line-art styles and concepts each."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from color_book_generator.config import CaptionConfig, PageSizeName, ProcessingConfig

PresetCategory = Literal[
    "cozy",
    "kids",
    "manga",
    "manhwa",
    "kawaii",
    "adult",
    "custom",
]

StoryMode = Literal["narrative", "collection"]


class StylePreset(BaseModel):
    id: str
    name: str
    category: PresetCategory = "cozy"
    tagline: str = ""
    description: str = ""
    badge: str = ""
    page_size: PageSizeName = "square_8.5x8.5"
    context_prompt: str
    negative_prompt: str = (
        "color, gradient, shading, gray tones, halftone, screentone, watermark, signature, blurry"
    )
    # Style notes for the character model sheet (the turnaround layout is fixed in identity.py).
    character_sheet_prompt: str = (
        "Clean black-and-white line art matching the book style, consistent proportions, "
        "outfit and hair details."
    )
    concept_prompt: str = ""
    default_mode: StoryMode = "collection"
    cast: int = Field(default=2, ge=0, le=4)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    caption: CaptionConfig = Field(default_factory=CaptionConfig)
    blank_verso: bool = False
    include_title_page: bool = True
    include_belongs_to_page: bool = True
    include_color_test_page: bool = True
    builtin: bool = True


CATEGORY_META: list[dict[str, str]] = [
    {
        "id": "cozy",
        "name": "Bold & Easy / Cozy",
        "tagline": "Traço grosso e cenas aconchegantes para marcadores.",
    },
    {
        "id": "kids",
        "name": "Infantil",
        "tagline": "Do toddler ao pequeno explorador de 9 anos.",
    },
    {
        "id": "manga",
        "name": "Mangá",
        "tagline": "Shonen, shoujo, seinen, josei e mais.",
    },
    {
        "id": "manhwa",
        "name": "Manhwa",
        "tagline": "Romance, ação, murim e fantasia coreana.",
    },
    {
        "id": "kawaii",
        "name": "Kawaii",
        "tagline": "Chibi, comidinhas, mascotes e adesivos.",
    },
    {
        "id": "adult",
        "name": "Adulto",
        "tagline": "Mandalas, zentangle, botânico e vitral.",
    },
]

CATEGORY_PAGE_LAYOUT: dict[str, str] = {
    "cozy": "single_full",
    "kids": "single_caption",
    "manga": "manga_action",
    "manhwa": "manhwa_editorial",
    "kawaii": "kawaii_stickers",
    "adult": "adult_medallion",
}


def default_page_layout_for_category(category: str) -> str:
    """Return the implicit page-layout preset for a book style category."""
    return CATEGORY_PAGE_LAYOUT.get(category, "single_full")

_RULES = (
    "Pure black line art on a pure white background, clean closed contours ready for coloring, "
    "zero color, zero gray fills, zero shading, no text."
)


def _cap(
    enabled: bool,
    style: Literal["balloon", "caption"],
    font: Literal["sans", "serif", "mono"],
    bold: bool,
    size: float,
) -> CaptionConfig:
    return CaptionConfig(
        enabled=enabled,
        style=style,
        position="bottom",
        font_family=font,
        bold=bold,
        font_size_ratio=size,
    )


_CATEGORY_DEFAULTS: dict[str, dict[str, Any]] = {
    "cozy": {
        "page_size": "square_8.5x8.5",
        "negative": (
            "color, shading, hatching, crosshatching, thin messy lines, gray, gradient, "
            "complex tiny details, realistic photo, text, watermark"
        ),
        "sheet": (
            "Cozy bold-and-easy look: thick uniform black outlines, soft rounded cute "
            "proportions, simple memorable outfit, large open areas to color."
        ),
        "processing": ProcessingConfig(
            threshold="otsu",
            denoise=True,
            min_component_px=36,
            crop_to_content=True,
            target_inner_margin_ratio=0.06,
        ),
        "caption": _cap(True, "caption", "sans", True, 0.032),
    },
    "kids": {
        "page_size": "letter_8.5x11",
        "negative": (
            "color, shading, scary, dark, complex background, tiny details, gray, gradient, text, watermark"
        ),
        "sheet": (
            "Children's picture-book look: bold clean black outlines, friendly expressive eyes, "
            "simple outfit with few large shapes, no tiny details."
        ),
        "processing": ProcessingConfig(
            threshold="adaptive",
            denoise=True,
            min_component_px=28,
            crop_to_content=True,
            target_inner_margin_ratio=0.06,
        ),
        "caption": _cap(True, "caption", "sans", False, 0.032),
    },
    "manga": {
        "page_size": "6x9",
        "negative": (
            "color, screentone, halftone dots, gray shading, solid black areas, blurry, "
            "cluttered background, excessive tiny details, heavy crosshatching, overcrowded panels, "
            "speech bubble, japanese text, watermark, messy sketch"
        ),
        "sheet": (
            "Clean Japanese manga settei look: smooth crisp black ink lines, simple iconic "
            "hairstyle, clean outfit with few folds (no excessive straps, buckles, pouches or "
            "tiny clutter), no screentones."
        ),
        "processing": ProcessingConfig(
            threshold="adaptive",
            denoise=False,
            min_component_px=12,
            crop_to_content=True,
            target_inner_margin_ratio=0.05,
        ),
        "caption": _cap(True, "balloon", "sans", False, 0.03),
    },
    "manhwa": {
        "page_size": "6x9",
        "negative": (
            "color, cel shading, gradient, glowing effects, gray tones, screentone, "
            "korean text, speech bubbles, watermark, sketchy lines"
        ),
        "sheet": (
            "Korean manhwa look: ultra-clean refined black ink lines, elegant proportions, crisp "
            "hair and costume lines."
        ),
        "processing": ProcessingConfig(
            threshold="adaptive",
            denoise=False,
            min_component_px=12,
            crop_to_content=True,
            target_inner_margin_ratio=0.05,
        ),
        "caption": _cap(True, "caption", "serif", False, 0.03),
    },
    "kawaii": {
        "page_size": "square_8.5x8.5",
        "negative": (
            "color, shading, realistic proportions, dark mood, gray, gradient, text, watermark, messy lines"
        ),
        "sheet": (
            "Kawaii chibi look: smooth medium-thick black outlines, oversized round head, tiny "
            "body, adorable simple face."
        ),
        "processing": ProcessingConfig(
            threshold="otsu",
            denoise=True,
            min_component_px=24,
            crop_to_content=True,
            target_inner_margin_ratio=0.06,
        ),
        "caption": _cap(True, "caption", "sans", True, 0.032),
    },
    "adult": {
        "page_size": "letter_8.5x11",
        "negative": (
            "color, gray shading, blurry, thick childish outlines, photo, gradient, text, watermark"
        ),
        "sheet": (
            "Detailed editorial/fantasy look: fine crisp black ink lines, ornamental outfit "
            "details that stay closed and colorable."
        ),
        "processing": ProcessingConfig(
            threshold="adaptive",
            denoise=False,
            min_component_px=8,
            crop_to_content=True,
            target_inner_margin_ratio=0.05,
        ),
        "caption": _cap(True, "caption", "serif", False, 0.028),
    },
}


def _style(
    *,
    id: str,
    name: str,
    category: PresetCategory,
    badge: str,
    tagline: str,
    context: str,
    concept: str,
    mode: StoryMode = "collection",
    cast: int = 2,
    page_size: PageSizeName | None = None,
    threshold: Literal["adaptive", "otsu", "fixed"] | None = None,
    denoise: bool | None = None,
    min_px: int | None = None,
    margin: float | None = None,
    negative_extra: str = "",
    caption: CaptionConfig | None = None,
    rules: str | None = None,
) -> StylePreset:
    base = _CATEGORY_DEFAULTS[category]
    overrides = {
        key: value
        for key, value in {
            "threshold": threshold,
            "denoise": denoise,
            "min_component_px": min_px,
            "target_inner_margin_ratio": margin,
        }.items()
        if value is not None
    }
    negative = base["negative"]
    if negative_extra:
        negative = f"{negative}, {negative_extra}"
    return StylePreset(
        id=id,
        name=name,
        category=category,
        badge=badge,
        tagline=tagline,
        description=tagline,
        page_size=page_size or base["page_size"],
        context_prompt=f"{context} {rules or _RULES}",
        negative_prompt=negative,
        character_sheet_prompt=base["sheet"],
        concept_prompt=concept,
        default_mode=mode,
        cast=cast,
        processing=base["processing"].model_copy(update=overrides),
        caption=caption or base["caption"].model_copy(),
    )


_BUILTIN_PRESETS: list[StylePreset] = [
    # ---------------------------------------------------------------- Cozy
    _style(
        id="bold_easy_cozy",
        name="Bold & Easy",
        category="cozy",
        badge="Base",
        tagline="Traços grossos, formas arredondadas e espaços amplos para marcadores.",
        context=(
            "Cozy bold and easy coloring book page in a cute hygge aesthetic. Thick uniform bold "
            "black outlines, rounded friendly shapes, a few simple charming cozy props, large open "
            "white areas designed for alcohol markers, about 15 to 30 colorable regions."
        ),
        concept=(
            "Everyday cozy moments of one adorable character: baking, tea time, reading, "
            "gardening, rainy days at home."
        ),
        cast=1,
    ),
    _style(
        id="cozy_cottagecore",
        name="Cottagecore",
        category="cozy",
        badge="Rústico",
        tagline="Cozinhas ensolaradas, hortas, ervas e pães no campo.",
        context=(
            "Cottagecore coloring page: sunlit rustic kitchen or garden scene with wooden shelves, "
            "hanging herbs, baskets, jars, bread, wildflowers and a cozy window. Thick bold outlines, "
            "a medium amount of charming props, balanced composition, large open color areas."
        ),
        concept=(
            "A countryside cottage through the seasons: baking bread, picking herbs, making jam, "
            "gathering flowers, market day."
        ),
        cast=1,
    ),
    _style(
        id="cozy_little_corner",
        name="Little corner",
        category="cozy",
        badge="Cômodos",
        tagline="Cantinhos de casa vistos de lado, cheios de móveis e xícaras.",
        context=(
            "Little corner room coloring page: a single cozy room in side view or cut-away filled with "
            "cute furniture, shelves, plants, lamps, rugs, mugs and baking supplies, inhabited by one "
            "tiny character or pet. Thick outlines, orderly layered objects, every object clearly "
            "separated for coloring."
        ),
        concept=(
            "A different tiny room on every page: bakery nook, music room, craft corner, window seat, "
            "attic studio, self-care bathroom."
        ),
        cast=1,
    ),
    _style(
        id="cozy_rainy_nook",
        name="Janela com chuva",
        category="cozy",
        badge="Chuva",
        tagline="Janelas com chuva, livros, cobertores e bebidas quentes.",
        context=(
            "Rainy day reading nook coloring page: window with raindrops and clouds, window seat, "
            "stacked books, blankets, pillows, a steaming mug, a sleeping cat, a candle and string "
            "lights. Thick bold outlines, soft rounded shapes, generous open areas."
        ),
        concept=(
            "Slow rainy-day rituals: reading, tea, knitting, sleeping pets, journaling, soup on the stove."
        ),
        cast=1,
    ),
    _style(
        id="cozy_plant_greenhouse",
        name="Estufa e plantas",
        category="cozy",
        badge="Plantas",
        tagline="Estufas, vasos e jardins internos com folhagem generosa.",
        context=(
            "Planty greenhouse coloring page: glass greenhouse or indoor jungle with potted houseplants, "
            "hanging pots, a watering can, terracotta pots, succulents and big leaves with clearly drawn "
            "shapes, plus one cute small animal or gardener. Thick medium-bold outlines, big leaf shapes, "
            "open white areas."
        ),
        concept=(
            "A plant keeper's year: repotting, propagating cuttings, watering day, starting seeds, "
            "harvest, visiting the plant shop."
        ),
        cast=1,
    ),
    _style(
        id="cozy_bakery_cafe",
        name="Padaria e café",
        category="cozy",
        badge="Café",
        tagline="Balcões de doces, pães, bules e vitrines aconchegantes.",
        context=(
            "Cozy bakery and cafe coloring page: display counter with pastries, bread baskets, a coffee "
            "machine, cake stands, pendant lamps, a blank chalkboard shape and a cute baker. Thick bold "
            "outlines, simple rounded props, large open color areas."
        ),
        concept=(
            "A small neighborhood bakery from sunrise to closing: kneading dough, first batch, "
            "regular customers, decorating cakes, closing up."
        ),
        cast=2,
    ),
    # ---------------------------------------------------------------- Kids
    _style(
        id="kids_storybook",
        name="Historinha para colorir",
        category="kids",
        badge="Padrão",
        tagline="Uma história contada página a página: cena ilustrada e a frase da história embaixo.",
        context=(
            "Children's storybook coloring page for ages 3 to 8. Cute rounded cartoon style: the main "
            "character has big glossy expressive eyes with a small catchlight, soft wavy hair or mane, "
            "a clear emotion that matches the moment of the story. One full illustrated scene per page, "
            "the character large and centered, framed by a charming environment (big trees, mushrooms, "
            "flowers, meadows, mountains, clouds, rainbows, small animals) drawn with smooth clean "
            "medium-weight outlines and large open areas easy to color. Keep the lower part of the "
            "page clear and white so a story sentence can be printed below the illustration."
        ),
        concept=(
            "A heartwarming picture-book story with one main character and a gentle lesson "
            "(belonging, courage, friendship, being yourself). Each page is one beat of the story: "
            "write the page as 'Page N — Title', then 'Story:' with two to five short sentences of "
            "narration and dialogue, then 'Illustration:' describing the single scene. The caption is "
            "one short line of dialogue or narration from that page. The final page closes the story "
            "with 'The End' and a short moral."
        ),
        mode="narrative",
        cast=1,
        threshold="adaptive",
        denoise=False,
        min_px=12,
        caption=_cap(True, "caption", "sans", False, 0.032),
    ),
    _style(
        id="kids_classic",
        name="Clássico infantil",
        category="kids",
        badge="4 a 8 anos",
        tagline="Personagem simples e centralizado, com poucos elementos de fundo.",
        context=(
            "Children's coloring book page for ages 4 to 8. Single clear cheerful main subject, bold "
            "clean black outlines, large simple closed shapes, minimal uncluttered background with only "
            "two or three simple elements, generous white space around the edges."
        ),
        concept=(
            "A friendly character on small everyday adventures: a day at the park, visiting the farm, "
            "going to school, a birthday party."
        ),
        mode="narrative",
        cast=1,
    ),
    _style(
        id="kids_toddler",
        name="Toddler grosso",
        category="kids",
        badge="1 a 4 anos",
        tagline="Um objeto grande por página, contorno extra grosso e fundo vazio.",
        context=(
            "Toddler coloring page for ages 1 to 4. One single huge simple subject centered on the "
            "page, extra thick chunky black outlines, very few large shapes, completely empty white "
            "background, no tiny details, rounded blocky forms."
        ),
        concept=(
            "Single big familiar things, each alone on its page: animals, fruits, vehicles, toys, "
            "basic shapes. Captions are one simple word."
        ),
        cast=0,
        threshold="otsu",
        min_px=48,
        margin=0.08,
        caption=_cap(True, "caption", "sans", True, 0.04),
    ),
    _style(
        id="kids_animal_friends",
        name="Bichinhos sorridentes",
        category="kids",
        badge="Animais",
        tagline="Animais de desenho com expressões simples em cenas curtas.",
        context=(
            "Cute smiling cartoon animals coloring page: one or two friendly animals with big simple "
            "faces doing a simple action in a small scene (pond, barn, forest edge), thick clean "
            "outlines, simple background made of a few shapes."
        ),
        concept=(
            "Animal friends in simple stories: farm, forest, pond, ocean, jungle, pets at home."
        ),
        mode="narrative",
        cast=2,
    ),
    _style(
        id="kids_dino_adventure",
        name="Aventura detalhada",
        category="kids",
        badge="5 a 9 anos",
        tagline="Cenas com ambiente completo: dinossauros, selva e exploradores.",
        context=(
            "Detailed kids adventure coloring page for ages 5 to 9: dynamic scene with a rich "
            "environment (prehistoric jungle with volcano and ferns, ocean reef, outer space, pirate "
            "cove), layered foreground and background, thinner varied outlines, scales and textures, "
            "and a clear main character in action."
        ),
        concept=(
            "An adventure series with a young explorer and a creature companion: jungle, volcano, "
            "sea, space, treasure hunt."
        ),
        mode="narrative",
        cast=2,
        min_px=16,
        margin=0.05,
    ),
    _style(
        id="kids_fairytale",
        name="Conto de fadas",
        category="kids",
        badge="Fantasia",
        tagline="Castelos, fadas, dragões e criaturas amigáveis como em livro de histórias.",
        context=(
            "Storybook fairytale coloring page for children: a friendly dragon, princess or knight, "
            "castle towers, fairies, a magical forest, mushrooms and stars, rounded gentle shapes, "
            "medium outlines, clear main subject with a moderately detailed background."
        ),
        concept=(
            "A gentle fairytale quest with a hero and a magical friend: castle, enchanted forest, "
            "dragon cave, fairy village, royal party."
        ),
        mode="narrative",
        cast=2,
    ),
    _style(
        id="kids_learning",
        name="Aprender e colorir",
        category="kids",
        badge="ABC e formas",
        tagline="Letras, números e formas grandes, com um objeto por página.",
        context=(
            "Early learning coloring page: one very large hollow bubble letter or number paired with "
            "one big simple object that starts with that letter, thick bold outlines, no background "
            "clutter."
        ),
        concept=(
            "Alphabet A to Z, numbers 1 to 10 and basic shapes, each page pairing one large letter or "
            "number with one object. Captions are the object's name."
        ),
        cast=0,
        threshold="otsu",
        min_px=40,
        rules=(
            "Pure black line art on a pure white background, clean closed contours, zero color, zero "
            "gray fills, zero shading, no text other than the single large hollow letter or number."
        ),
    ),
    # --------------------------------------------------------------- Manga
    _style(
        id="manga_lineart",
        name="Shonen ação",
        category="manga",
        badge="Shonen",
        tagline="Traço forte e dinâmico, com poses de ação e linhas de movimento.",
        context=(
            "Shonen manga style illustration, bold dynamic ink linework with confident varied line "
            "weight, sharp angular hair, determined expressive eyes, energetic action pose and a few "
            "speed lines. NO screentones, NO solid black fills, outlines only."
        ),
        concept=(
            "A young hero chasing a dream with a rival and a loyal friend: training, tournament, "
            "first mission, rescue."
        ),
        mode="narrative",
        cast=2,
    ),
    _style(
        id="manga_shoujo",
        name="Shoujo romântico",
        category="manga",
        badge="Shoujo",
        tagline="Linhas delicadas, olhos grandes e flores decorativas.",
        context=(
            "Shoujo manga style illustration, delicate flowing fine linework, tall slender graceful "
            "figures, large detailed sparkling eyes with several catchlight shapes and long lashes, "
            "soft wavy hair, decorative floating flowers, petals, stars and bubbles around the "
            "characters. Outlines only, no screentones."
        ),
        concept=(
            "A gentle romance and friendship story: school festival, shared umbrella, a handwritten "
            "letter, cherry blossom walk, confession."
        ),
        mode="narrative",
        cast=2,
    ),
    _style(
        id="manga_seinen",
        name="Seinen realista",
        category="manga",
        badge="Seinen",
        tagline="Proporções realistas, cenários detalhados e atmosfera madura.",
        context=(
            "Seinen manga style illustration, realistic anatomy and grounded proportions, small "
            "restrained eyes, subtle expressions, intricate detailed urban or natural backgrounds with "
            "fine architectural lines, texture shown with sparse fine contour lines instead of heavy "
            "hatching, every region closed for coloring. No screentones, no solid black."
        ),
        concept=(
            "A quiet grounded drama or detective story: night city, train station, mountain village, "
            "old workshop, ramen shop."
        ),
        mode="narrative",
        cast=2,
        min_px=8,
    ),
    _style(
        id="manga_josei",
        name="Josei elegante",
        category="manga",
        badge="Josei",
        tagline="Moda, interiores e personagens adultos com traço limpo.",
        context=(
            "Josei manga style illustration, clean refined linework, naturalistic adult proportions, "
            "elegant hands and collarbones, understated expressive eyes, fashionable outfits with clear "
            "fabric folds, chic cafe and apartment interiors. Outlines only, no screentones."
        ),
        concept=(
            "Slice-of-life stories of adults: coffee shop, shared apartment, office, travel, "
            "bookstore date."
        ),
        mode="narrative",
        cast=2,
    ),
    _style(
        id="manga_chibi_gag",
        name="Chibi cômico",
        category="manga",
        badge="Gag",
        tagline="Personagens super deformados com reações exageradas.",
        context=(
            "Comedy manga super-deformed chibi style, two-to-three-head-tall characters, simple bold "
            "lines, exaggerated reaction faces (sweat drops, anger marks, blushing), tiny props, "
            "dynamic comedic poses, simple backgrounds. Outlines only, no screentones."
        ),
        concept=(
            "Gag comedy slice of life: club activities, cooking disasters, school trip, pet chaos."
        ),
        cast=2,
        min_px=20,
    ),
    _style(
        id="manga_adventure_classic",
        name="Aventura clássica",
        category="manga",
        badge="Revista",
        tagline="Rostos expressivos e nanquim marcante de aventura de revista semanal.",
        context=(
            "Clean Japanese weekly adventure manga style: smooth confident black ink outlines, "
            "expressive faces with clear eyes and dynamic hair shapes, clean uncluttered compositions "
            "with simple minimal backgrounds (open sky, sea horizon, simple deck or cliff silhouettes) "
            "and generous white breathing space, crisp speed lines for action beats. "
            "NO heavy cross-hatching, NO cluttered dense props, NO screentones, outlines only."
        ),
        concept=(
            "A crew of young human adventurers sailing and exploring: harbor town, island, "
            "treasure map, storm at sea, big feast."
        ),
        mode="narrative",
        cast=3,
    ),
    # -------------------------------------------------------------- Manhwa
    _style(
        id="manhwa_lineart",
        name="Romance fantasy",
        category="manhwa",
        badge="Otome",
        tagline="Nobres elegantes, vestidos e cabelos fluidos em traço delicado.",
        context=(
            "Korean romance-fantasy manhwa style, ultra-clean delicate digital line art, tall slender "
            "elegant characters with long necks, large glossy detailed eyes, flowing long hair, "
            "regency or royal court gowns, jewelry, ruffles and ornate details, floating rose petals "
            "and sparkles drawn as outlines."
        ),
        concept=(
            "A noble heroine and a duke in a royal court: grand ball, garden tea, masquerade, rebirth "
            "as a villainess, secret letters."
        ),
        mode="narrative",
        cast=2,
    ),
    _style(
        id="manhwa_modern_romance",
        name="Romance moderno",
        category="manhwa",
        badge="Cotidiano",
        tagline="Moda atual, cafés e cidade com personagens estilosos.",
        context=(
            "Modern Korean romance webtoon style, clean crisp digital linework with variable line "
            "weight (thicker outer contours, finer inner details), tall stylish characters in "
            "contemporary fashion, expressive soft eyes, cafe, rooftop and city street backgrounds."
        ),
        concept=(
            "Contemporary romance and friendship: office, campus, cafe, rooftop, subway, rainy umbrella."
        ),
        mode="narrative",
        cast=2,
    ),
    _style(
        id="manhwa_action_hunter",
        name="Ação e caçadores",
        category="manhwa",
        badge="Hunter",
        tagline="Linhas angulares, músculos definidos e impactos fortes.",
        context=(
            "Korean action hunter webtoon style, sharp angular aggressive linework, athletic defined "
            "musculature, intense expressions, dramatic low angles, ruined city streets and dungeon "
            "gates, shattered debris and impact shapes drawn as clean closed outlines, no glow effects."
        ),
        concept=(
            "A hunter rising through the ranks: dungeon gate, boss raid, guild hall, rooftop duel, "
            "awakening."
        ),
        mode="narrative",
        cast=2,
    ),
    _style(
        id="manhwa_murim",
        name="Murim",
        category="manhwa",
        badge="Espada",
        tagline="Espadachins, túnicas esvoaçantes e traço de pincel.",
        context=(
            "Korean murim martial-arts manhwa style, expressive ink-brush influenced linework "
            "converted to clean black outlines, swordsmen and masters with topknots and long flowing "
            "hair, layered robes with billowing sleeves, mountain sect gates, bamboo and pine, plum "
            "blossom petals, sword motion shown with flowing contour curves. No ink wash fills, no gray."
        ),
        concept=(
            "A disciple's path in a mountain sect: sword training, tournament, tea house, mountain "
            "pass, the master's lesson."
        ),
        mode="narrative",
        cast=2,
        min_px=10,
    ),
    _style(
        id="manhwa_fantasy_rpg",
        name="Fantasia e RPG",
        category="manhwa",
        badge="Épico",
        tagline="Armaduras, círculos mágicos e cenários épicos.",
        context=(
            "Korean high-fantasy adventure manhwa style, crisp structured linework, heroic "
            "proportions, detailed armor, capes, staffs and swords, magic circles and rune patterns "
            "drawn as clean outlines, grand castles, fantasy cities and dragons. No glow, no gradients."
        ),
        concept=(
            "An adventurer party: guild registration, dungeon crawl, dragon encounter, magic academy, "
            "tavern night."
        ),
        mode="narrative",
        cast=3,
    ),
    _style(
        id="manhwa_historical",
        name="Histórico coreano",
        category="manhwa",
        badge="Joseon",
        tagline="Hanbok, palácios e telhados curvos da era Joseon.",
        context=(
            "Korean historical sageuk manhwa style, refined delicate linework, characters in layered "
            "hanbok with ribbons, gat hats and hairpins, palace courtyards with curved tiled roofs, "
            "lanterns, folding screens, pavilions, lotus ponds and pine trees."
        ),
        concept=(
            "Court and village stories in the Joseon era: palace garden, market day, lantern "
            "festival, scholar's study, secret meeting."
        ),
        mode="narrative",
        cast=2,
    ),
    # -------------------------------------------------------------- Kawaii
    _style(
        id="kawaii_chibi",
        name="Chibi",
        category="kawaii",
        badge="Personagens",
        tagline="Personagens chibi com olhos grandes e contornos limpos.",
        context=(
            "Kawaii chibi coloring page. Adorable chibi proportions with oversized heads and tiny "
            "bodies, big sparkling eyes, small nose and mouth, smooth medium-thick outlines, cute "
            "themed props and sparkles."
        ),
        concept=(
            "Chibi friends in cute daily scenes: picnic, sleepover, bakery, festival, pet day."
        ),
        cast=2,
    ),
    _style(
        id="kawaii_food_faces",
        name="Comidas com carinha",
        category="kawaii",
        badge="Comidinhas",
        tagline="Bubble tea, torradas e ramen com expressões fofas.",
        context=(
            "Kawaii smiling food coloring page: bubble tea cups, toast, ramen bowls, sushi, cupcakes, "
            "avocados and fruits with tiny happy faces, rosy cheeks and little arms, round soft "
            "shapes, thick smooth outlines, simple sparkles and hearts around them."
        ),
        concept=(
            "Themed food collections: bakery, cafe, sushi bar, picnic, dessert party, breakfast."
        ),
        cast=0,
    ),
    _style(
        id="kawaii_mascot_minimal",
        name="Mascote minimalista",
        category="kawaii",
        badge="Mascote",
        tagline="Personagem simples com laço, capuz ou acessório marcante.",
        context=(
            "Kawaii mascot style coloring page inspired by minimalist character merchandise: a single "
            "iconic round mascot character with very simple dot eyes and a tiny mouth, bold clean "
            "uniform outlines, one signature accessory such as a bow, hood or star, minimal props, "
            "lots of white space."
        ),
        concept=(
            "A small mascot and friends in simple everyday situations: hobbies, weather, seasons, "
            "snacks, sleep."
        ),
        cast=2,
    ),
    _style(
        id="kawaii_sticker_sheet",
        name="Folha de adesivos",
        category="kawaii",
        badge="Sticker",
        tagline="Vários adesivos por página, com contorno grosso e silhueta simples.",
        context=(
            "Kawaii sticker sheet coloring page: a tidy arrangement of 6 to 9 separate cute die-cut "
            "style stickers (characters, foods, objects) each with a thick clear outline and simple "
            "silhouette, evenly spaced, expressive poses, no overlap."
        ),
        concept=(
            "Sticker sheets by theme: animals, foods, weather, plants, hobbies, seasons. Leave "
            "caption_text empty on every page."
        ),
        cast=0,
        caption=_cap(False, "caption", "sans", True, 0.032),
    ),
    _style(
        id="kawaii_chunky_animals",
        name="Bichos fofos",
        category="kawaii",
        badge="Tendência",
        tagline="Capivaras, axolotes, vacas e gatos rabugentos em formas redondas.",
        context=(
            "Kawaii chunky animals coloring page: round chubby capybara, axolotl, highland cow, "
            "grumpy cat, frog, duck or hedgehog with simple dot eyes and tiny smiles or grumpy brows, "
            "plump simple bodies, thick smooth outlines, a few matching props like mugs, plants or "
            "tiny hats."
        ),
        concept=(
            "Chunky animal friends doing cozy activities: soaking in a hot spring, baking, "
            "gardening, napping, reading."
        ),
        cast=2,
    ),
    _style(
        id="kawaii_dreamy_sky",
        name="Céu sonhador",
        category="kawaii",
        badge="Pastel",
        tagline="Nuvens, estrelas, lua e arco-íris com bichinhos dormindo.",
        context=(
            "Dreamy kawaii sky coloring page: fluffy clouds, a crescent moon, twinkling stars, "
            "rainbows and floating hearts with tiny sleeping animals or a character on a cloud bed, "
            "soft rounded shapes, smooth outlines, plenty of open areas for pastel colors."
        ),
        concept=(
            "Bedtime and sky themes: moon lullaby, cloud beds, rainbow rain, star collecting, dream travel."
        ),
        cast=1,
    ),
    # --------------------------------------------------------------- Adult
    _style(
        id="adult_intricate",
        name="Mandala",
        category="adult",
        badge="Simetria",
        tagline="Mandalas simétricos com padrões repetidos a partir do centro.",
        context=(
            "Intricate mandala coloring page: perfectly radial symmetric circular design radiating "
            "from a central core with repeating petals, rings, stars, teardrops and lace patterns, "
            "fine crisp uniform linework, balanced composition filling the page."
        ),
        concept=(
            "Mandala collections by motif: floral, celestial, animal, geometric, seasonal. Captions "
            "are a short calming word or quote."
        ),
        cast=0,
        page_size="square_8.5x8.5",
    ),
    _style(
        id="adult_zentangle",
        name="Zentangle",
        category="adult",
        badge="Padrões",
        tagline="Padrões repetitivos densos, organizados em faixas e formas.",
        context=(
            "Zentangle style coloring page: a large simple outline shape (animal, leaf, vase or heart) "
            "filled with different structured repetitive tangle patterns in separate sections (lines, "
            "dots, curves, grids, scales, swirls), each section clearly closed, fine pen linework, "
            "dense but crisp."
        ),
        concept=(
            "Pattern pages built on a themed silhouette: animals, plants, ocean, birds, feathers. "
            "Captions are a short calming quote."
        ),
        cast=0,
        min_px=6,
    ),
    _style(
        id="adult_botanical",
        name="Botânico",
        category="adult",
        badge="Natureza",
        tagline="Flores, folhagens, borboletas e jardins com detalhe natural.",
        context=(
            "Botanical garden coloring page: lush stylized flowers, leaves, ferns, tropical foliage, "
            "succulents, butterflies and birds, overlapping natural layers with fine detailed veins "
            "and petals, clean closed shapes, elegant composition with some breathing room."
        ),
        concept=(
            "Botanical themes: wildflower meadow, tropical jungle, houseplants, herb garden, bird and "
            "bloom, the four seasons. Captions are a short nature quote."
        ),
        cast=0,
    ),
    _style(
        id="adult_art_nouveau",
        name="Art nouveau",
        category="adult",
        badge="Vintage",
        tagline="Curvas sinuosas, flores estilizadas e figuras elegantes em moldura.",
        context=(
            "Art Nouveau poster style coloring page: sinuous whiplash curves, stylized flowers and "
            "vines framing an elegant figure or animal in an ornamental arch or circular frame, "
            "flowing hair interwoven with blossoms, decorative borders, graphic clear linework with "
            "defined colorable spaces."
        ),
        concept=(
            "Muses of the seasons, flowers, moon and sun, and birds, each in an ornamental frame."
        ),
        cast=1,
    ),
    _style(
        id="adult_fantasy",
        name="Reinos de fantasia",
        category="adult",
        badge="Fantasia",
        tagline="Dragões, fadas, castelos e florestas encantadas em cenas detalhadas.",
        context=(
            "Fantasy realm coloring page for adults: detailed mythical scene with dragons, unicorns, "
            "fairies, wizards, castles, an enchanted forest or a celestial map, rich textures such as "
            "scales, feathers, stone and bark, atmospheric composition, fine to medium linework, all "
            "regions closed."
        ),
        concept=(
            "A chain of fantasy realms: dragon mountain, fairy glade, wizard tower, sea kingdom, moon temple."
        ),
        cast=1,
    ),
    _style(
        id="adult_stained_glass",
        name="Vitral",
        category="adult",
        badge="Marcadores",
        tagline="Segmentos separados por linhas pesadas, como um vitral.",
        context=(
            "Stained glass coloring page: design divided into bold segmented shapes separated by thick "
            "heavy black lead lines, a subject such as a flower, animal, landscape or geometric "
            "rosette framed like a window, every segment fully closed and clearly separated, no fine "
            "hatching."
        ),
        concept=(
            "Window panes: flowers, birds, owls, sunsets, mandala rosettes, lighthouse. Captions are "
            "a short calming word."
        ),
        cast=0,
        threshold="otsu",
        min_px=30,
    ),
]


def builtin_presets() -> list[StylePreset]:
    return [preset.model_copy(deep=True) for preset in _BUILTIN_PRESETS]


def get_preset(
    preset_id: str, custom: list[StylePreset] | None = None
) -> StylePreset | None:
    for preset in custom or []:
        if preset.id == preset_id:
            return preset.model_copy(deep=True)
    for preset in _BUILTIN_PRESETS:
        if preset.id == preset_id:
            return preset.model_copy(deep=True)
    return None
