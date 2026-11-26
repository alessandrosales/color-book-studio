from color_book_generator.config import BookConfig
from color_book_generator.prompts import build_page_prompts, default_subjects


def test_prompt_count_and_numbering() -> None:
    cfg = BookConfig(theme="forest animals", pages=5)
    prompts = build_page_prompts(cfg)
    assert len(prompts) == 5
    assert [p.index for p in prompts] == [1, 2, 3, 4, 5]
    assert all("forest animals" in p.prompt for p in prompts)
    assert prompts[0].negative_prompt == cfg.negative_prompt


def test_subject_rotation_is_deterministic() -> None:
    cfg = BookConfig(theme="fox", pages=10)
    assert default_subjects(cfg) == default_subjects(cfg)
    assert len(default_subjects(cfg)) == 10


def test_explicit_subjects_are_padded_and_truncated() -> None:
    cfg = BookConfig(theme="fox", pages=4)
    prompts = build_page_prompts(cfg, subjects=["a", "b"])
    assert [p.subject for p in prompts] == ["a", "b", "a", "b"]

    cfg2 = BookConfig(theme="fox", pages=2)
    prompts2 = build_page_prompts(cfg2, subjects=["a", "b", "c"])
    assert [p.subject for p in prompts2] == ["a", "b"]
