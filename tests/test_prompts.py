from color_book_generator.config import BookConfig
from color_book_generator.prompts import (
    build_page_prompts,
    compose_page_prompt,
    default_subjects,
    effective_negative_prompt,
    line_standard_directive,
)
from color_book_generator.standards import LINE_STANDARDS


def test_prompt_count_and_numbering() -> None:
    cfg = BookConfig(theme="forest animals", pages=5)
    prompts = build_page_prompts(cfg)
    assert len(prompts) == 5
    assert [p.index for p in prompts] == [1, 2, 3, 4, 5]
    assert all("forest animals" in p.prompt for p in prompts)
    assert prompts[0].negative_prompt == cfg.negative_prompt


def test_subject_rotation_is_deterministic() -> None:
    cfg = BookConfig(theme="fox", pages=10)
    first, second = default_subjects(cfg), default_subjects(cfg)
    assert first == second
    assert len(default_subjects(cfg)) == 10


def test_explicit_subjects_are_padded_and_truncated() -> None:
    cfg = BookConfig(theme="fox", pages=4)
    prompts = build_page_prompts(cfg, subjects=["a", "b"])
    assert [p.subject for p in prompts] == ["a", "b", "a", "b"]

    cfg2 = BookConfig(theme="fox", pages=2)
    prompts2 = build_page_prompts(cfg2, subjects=["a", "b", "c"])
    assert [p.subject for p in prompts2] == ["a", "b"]


def test_manga_layout_embeds_text_inside_speech_balloons_and_strips_no_text() -> None:
    context = "Shoujo manga style illustration. Pure black line art, zero shading, no text."
    prompt = compose_page_prompt(
        context,
        "two students sharing an umbrella in the rain",
        1,
        10,
        page_layout="manga_action",
        caption_text="Even the rain feels warm when we walk together.",
    )
    assert "no text" not in prompt.lower()
    assert "Even the rain feels warm when we walk together." in prompt
    assert "speech balloon" in prompt.lower()
    assert "never draw empty or blank speech balloons" in prompt.lower()

    neg = effective_negative_prompt(
        "color, screentone, speech bubble, watermark",
        has_embedded_text=True,
        page_layout="manga_action",
    )
    assert "speech bubble," not in neg.lower()
    assert "empty speech bubbles" in neg.lower()
    assert "watermark" in neg.lower()


def test_manhwa_layout_splits_multi_line_dialogue_into_gutter_balloons() -> None:
    prompt = compose_page_prompt(
        "Korean manhwa line art, no text.",
        "heroine holding a lantern in the garden",
        2,
        10,
        page_layout="manhwa_editorial",
        caption_text="Don't turn back. | I'm ready.",
    )
    assert 'Balloon 1: "Don\'t turn back."' in prompt
    assert 'Balloon 2: "I\'m ready."' in prompt
    assert "gutter" in prompt.lower()


def test_kids_and_other_layouts_embed_text_in_exact_pattern_location() -> None:
    kids = compose_page_prompt(
        "Children's coloring page, no text.",
        "a happy fox in a meadow",
        1,
        5,
        page_layout="single_caption",
        caption_text="Little Fox found a new friend.",
    )
    assert "Storybook text line" in kids
    # The storybook pattern: a free vignette with the text on the white page, never boxed.
    assert "NO frame, NO border" in kids
    assert "separated by a crisp horizontal divider" not in kids
    assert "Little Fox found a new friend." in kids

    cozy = compose_page_prompt(
        "Cozy bold and easy page, no text.",
        "a bear drinking tea",
        1,
        5,
        page_layout="single_full",
        caption_text="Rainy day tea",
    )
    assert "rounded square border" in cozy
    assert "Cozy framed caption" in cozy
    assert "Rainy day tea" in cozy

    kawaii = compose_page_prompt(
        "Kawaii chibi page, no text.",
        "four cute snacks",
        1,
        5,
        page_layout="kawaii_stickers",
        caption_text="Sweet treats",
    )
    assert "Kawaii integrated label" in kawaii
    assert "Sweet treats" in kawaii

    adult = compose_page_prompt(
        "Intricate mandala, no text.",
        "floral medallion",
        1,
        5,
        page_layout="adult_medallion",
        caption_text="Breathe slowly",
        caption_font_family="serif",
    )
    assert "Editorial quote" in adult
    assert "Breathe slowly" in adult
    assert "serif" in adult.lower()


def test_empty_caption_forbids_empty_speech_balloons_in_manga() -> None:
    prompt = compose_page_prompt(
        "Shoujo manga style, no text.",
        "quiet cherry blossom path",
        1,
        5,
        page_layout="manga_action",
        caption_text="",
    )
    assert "no text and no speech balloons" in prompt.lower()


def test_line_standard_directive_states_the_checked_targets() -> None:
    kids = line_standard_directive(LINE_STANDARDS["3-5"])
    assert "0.7 to 1.4 mm" in kids
    assert "marker" not in kids  # thick marker strokes swallow small details
    assert "at least 5 mm across" in kids
    assert "at most about 90 small closed shapes" in kids
    assert "NO hatching" in kids

    adult = line_standard_directive(LINE_STANDARDS["intricate"])
    assert "0.3 to 0.6 mm" in adult
    assert "at most about" not in adult  # dense detail is the point of adult pages


def test_no_line_standard_goes_below_kdp_minimum() -> None:
    from color_book_generator.standards import KDP_MIN_LINE_MM

    assert all(std.line_min_mm >= KDP_MIN_LINE_MM for std in LINE_STANDARDS.values())
