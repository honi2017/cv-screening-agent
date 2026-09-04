from screen.text import (
    bullet_hash,
    contains_quote,
    extract_bullets,
    find_section,
    jaccard,
    normalize,
)

MD = """
# Jane Doe

## Experience

### Senior Engineer, Acme (2019-2023)
- Built a payments pipeline handling 4M events/day
* Led SSO rollout across 12 enterprise clients

### Engineer, Beta Corp (2016-2019)
+ Maintained SFTP ingestion jobs

## Skills
Python, Go, PostgreSQL
"""


def test_extract_bullets_handles_all_markers():
    bullets = extract_bullets(MD)
    assert bullets == [
        "Built a payments pipeline handling 4M events/day",
        "Led SSO rollout across 12 enterprise clients",
        "Maintained SFTP ingestion jobs",
    ]


def test_extract_bullets_ignores_headings_and_prose():
    assert "Python, Go, PostgreSQL" not in extract_bullets(MD)


def test_normalize_lowercases_strips_punctuation_and_collapses_space():
    assert normalize("  Led   SSO rollout, across 12 clients!  ") == "led sso rollout across 12 clients"


def test_bullet_hash_is_stable_and_normalization_insensitive():
    assert bullet_hash("Led SSO rollout") == bullet_hash("  led   sso   rollout!  ")
    assert bullet_hash("Led SSO rollout") != bullet_hash("Led SAML rollout")
    assert len(bullet_hash("x")) == 16


def test_jaccard_identical_and_disjoint():
    assert jaccard("built a payments pipeline", "Built a payments pipeline!") == 1.0
    assert jaccard("alpha beta", "gamma delta") == 0.0


def test_jaccard_near_duplicate_above_threshold():
    a = "Spearheaded cross-functional initiatives resulting in 40% efficiency gains"
    b = "Spearheaded cross functional initiatives resulting in 45% efficiency gains"
    assert jaccard(a, b) >= 0.85


def test_jaccard_empty_inputs_are_zero():
    assert jaccard("", "anything") == 0.0
    assert jaccard("", "") == 0.0


def test_find_section_returns_body_until_next_heading():
    body = find_section(MD, ["skills"])
    assert body is not None
    assert "Python, Go, PostgreSQL" in body
    assert "Experience" not in body


def test_find_section_is_case_insensitive_and_returns_none_when_absent():
    assert find_section(MD, ["EXPERIENCE"]) is not None
    assert find_section(MD, ["publications"]) is None


def test_find_section_handles_hash_inside_heading_text():
    # "C#" in a heading must not inflate the computed heading level, or the
    # section boundary lands in the wrong place.
    md = "## Skills\n### C# and .NET\n- LINQ\n## Education\nBSc\n"
    body = find_section(md, ["skills"])
    assert body is not None
    assert "LINQ" in body
    assert "BSc" not in body


def test_contains_quote_ignores_whitespace_differences():
    assert contains_quote("Built a  payments\npipeline", "Built a payments pipeline")
    assert not contains_quote("Built a payments pipeline", "Built a billing pipeline")


def test_contains_quote_empty_quote_is_false():
    assert not contains_quote("anything", "")


def test_jaccard_numeric_only_tokens_ignored():
    # Pairs differing only in numeric tokens should score 1.0 because
    # jaccard filters out purely-digit tokens before comparison.
    assert jaccard("handled 40 million events", "handled 45 million events") == 1.0
    assert jaccard("grew revenue 5 percent", "grew revenue 50 percent") == 1.0
    assert jaccard("grew revenue 500 percent", "grew revenue 5000 percent") == 1.0


def test_jaccard_magnitude_difference_boundary():
    # Known limitation: magnitude differences (5 vs 50 engineers) are invisible.
    # This is accepted because Task 4 applies a min_words=6 floor to intra-CV
    # duplicate detection, which excludes short numeric-difference pairs while
    # keeping longer sentence-template matches in scope. A future change to this
    # behaviour should verify it does not weaken Task 4's duplicate-elimination.
    assert jaccard("led team of 5 engineers", "led team of 50 engineers") == 1.0
