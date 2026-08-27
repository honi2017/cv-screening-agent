from pathlib import Path

from screen.config import load_role
from screen.signals import (
    count_skills,
    find_placeholders,
    intra_cv_duplicates,
    power_verb_density,
    round_metric_ratio,
    template_metadata_signal,
)

CFG = load_role(Path(__file__).resolve().parents[1] / "roles" / "fde")


def test_find_placeholders_catches_bracketed_tokens():
    text = "Excited to join [Company Name] as a [Position Title]. Contact [Your Email]."
    kinds = [p["match"] for p in find_placeholders(text)]
    assert "[Company Name]" in kinds
    assert "[Position Title]" in kinds
    assert "[Your Email]" in kinds


def test_find_placeholders_catches_lorem_and_xx_metrics():
    found = find_placeholders("Lorem ipsum dolor. Improved throughput by XX%.")
    matches = " ".join(p["match"].lower() for p in found)
    assert "lorem ipsum" in matches
    assert "xx%" in matches


def test_find_placeholders_catches_mustache_and_insert():
    found = find_placeholders("Hello {{name}}, [Insert metric here].")
    matches = " ".join(p["match"].lower() for p in found)
    assert "{{name}}" in matches
    assert "insert metric here" in matches


def test_find_placeholders_ignores_normal_brackets():
    # Real CVs cite things like "[1]" or "(2019-2023)"; these must not fire.
    assert find_placeholders("Published in JMLR [1]. Worked 2019-2023 (Acme).") == []


def test_find_placeholders_ignores_real_engineering_phrasing():
    # These are genuine engineering CV phrases, not unfilled templates. A Tier 1
    # hit here is a silent, unappealable rejection of a qualified applicant --
    # this broke an earlier version of these patterns, which matched bare
    # "insert" (a database verb) and any bracketed phrase containing a word
    # like "role" or "job" regardless of case. The delimiter requirement on
    # "insert" (must be wrapped in [], {}, or <>) and the capitalisation +
    # known-field-word requirement on bare bracketed labels are what keep
    # these out of scope.
    must_not_fire = [
        "Optimised batch insert operations for the ingestion pipeline",
        "Reduced insert latency from 400ms to 12ms on the orders table",
        "Migrated bulk insert jobs to COPY for 8x throughput",
        "Built [role-based access control] across 12 services",
        "Implemented [job queue] with Redis",
        "Shipped [Redis] and [Kafka] integrations",
        "Wrote the [title] parser for citations",
    ]
    for text in must_not_fire:
        assert find_placeholders(text) == [], text


def test_find_placeholders_catches_real_placeholders():
    must_fire = [
        "[Company Name]",
        "[Position Title]",
        "[Your Email]",
        "[Job Title]",
        "[FULL NAME]",
        "[Insert metric here]",
        "{INSERT COMPANY NAME}",
        "<CANDIDATE_NAME>",
        "{{name}}",
        "Lorem ipsum",
        "XX%",
    ]
    for text in must_fire:
        assert find_placeholders(text) != [], text


def test_intra_cv_duplicates_flags_near_identical_bullets():
    bullets = [
        "Spearheaded cross-functional initiatives resulting in 40% efficiency gains",
        "Spearheaded cross functional initiatives resulting in 45% efficiency gains",
        "Built a PostgreSQL replication service for the reporting suite",
    ]
    dups = intra_cv_duplicates(bullets, threshold=0.85)
    assert len(dups) == 1
    assert dups[0]["similarity"] >= 0.85


def test_intra_cv_duplicates_ignores_distinct_bullets():
    bullets = [
        "Built a PostgreSQL replication service",
        "Led SAML SSO rollout for 18 clients",
        "Ran SMTP deliverability improvements",
    ]
    assert intra_cv_duplicates(bullets, threshold=0.85) == []


def test_intra_cv_duplicates_min_words_floor_guards_numeric_blindness():
    # jaccard() drops purely-numeric tokens before comparing (screen/text.py), so
    # "led team of 5 engineers" vs "led team of 50 engineers" scores 1.0 even
    # though 5 and 50 are an order of magnitude apart -- a genuine
    # career-progression pair, not a lazy template swap. Since this function
    # feeds a hard-elimination gate, that false positive would wrongly
    # eliminate a real applicant. The min_words=6 default keeps both of these
    # 5-word bullets out of comparison entirely; do not remove the floor
    # without understanding this.
    bullets = ["led team of 5 engineers", "led team of 50 engineers"]
    assert intra_cv_duplicates(bullets, threshold=0.85) == []


def test_power_verb_density_high_for_slop_and_low_for_specifics():
    slop = [
        "Spearheaded cross-functional initiatives",
        "Leveraged cutting-edge technologies",
        "Orchestrated stakeholder alignment",
        "Facilitated seamless collaboration",
    ]
    real = [
        "Built the PostgreSQL to Snowflake replication service",
        "Ran SMTP deliverability, bounce rate 4.1% to 0.6%",
        "Rolled out Okta SAML for 18 clients",
        "Owned on-call for the ingestion platform",
    ]
    assert power_verb_density(slop) >= 0.75
    assert power_verb_density(real) <= 0.25


def test_round_metric_ratio_counts_only_round_percentages():
    round_only = ["improved by 40%", "reduced by 25%", "grew 50%"]
    specific = ["improved by 4.1%", "reduced 12 hours to 37 minutes", "grew 18.6%"]
    assert round_metric_ratio(round_only) == 1.0
    assert round_metric_ratio(specific) == 0.0


def test_round_metric_ratio_zero_when_no_metrics():
    assert round_metric_ratio(["built a thing", "owned a service"]) == 0.0


def test_power_verb_density_and_round_ratio_handle_empty():
    assert power_verb_density([]) == 0.0
    assert round_metric_ratio([]) == 0.0


def test_count_skills_splits_on_commas_and_pipes():
    md = "## Skills\nPython, Go | PostgreSQL; Snowflake, SFTP\n\n## Education\nBSc\n"
    assert count_skills(md) == 5


def test_count_skills_zero_without_section():
    assert count_skills("## Experience\n- did things\n") == 0


def test_template_metadata_signal_true_for_known_producer_and_fresh_file():
    meta = {"producer": "Canva", "creator": "Canva"}
    assert template_metadata_signal(meta, minutes_before_submission=5, cfg=CFG) is True


def test_template_metadata_signal_false_when_old_or_unknown_producer():
    assert template_metadata_signal({"producer": "Canva"}, 120, CFG) is False
    assert template_metadata_signal({"producer": "LaTeX"}, 5, CFG) is False
    assert template_metadata_signal({"producer": "Canva"}, None, CFG) is False
