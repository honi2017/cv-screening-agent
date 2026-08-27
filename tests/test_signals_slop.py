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


def test_find_placeholders_ignores_realistic_false_positive_classes():
    # Every one of these is real phrasing for a senior integration/consulting
    # engineer, not an unfilled template. A Tier 1 hit here is a silent,
    # unappealable rejection of a qualified applicant. Three earlier
    # word-list-based versions of this pattern were each defeated by one of
    # these classes in turn:
    #   - "insert" as a bare database verb ("batch insert operations")
    #   - lowercase generic brackets ("[role-based access control]", "[job queue]")
    #   - bracketed institution/place names containing a field-ish word
    #     ("[Ohio State University]", "[Penn State]")
    #   - NDA-anonymised employers, which put the field noun FIRST, not last
    #     ("[Company A]", "[Employer Redacted]", "[Candidate Matching]")
    #   - single-word domain labels, which have no second word at all
    #     ("[Email]", "[Address]", "[Date]", "[Degree]", "[PII]", "[ETL]")
    # The current rule -- a bracketed label counts only when it has at least
    # two words AND the field noun is the HEAD (last word) of the phrase --
    # is what keeps all of these out of scope: none of them end in a
    # recognised field noun, and the single-word ones never reach the
    # mandatory second-word requirement at all.
    must_not_fire = [
        # NDA-anonymised employers / candidate-facing product nouns: field
        # word present but not the head.
        "Built the integration platform for [Company A] (NDA, name withheld)",
        "Delivered SSO rollout for [Company Confidential]",
        "Worked at [Employer Redacted] as a senior engineer",
        "Built a [Candidate Matching] algorithm for the recruiting platform",
        "Owned the [Candidate Pipeline] service for the ATS integration",
        # Single-word domain labels: no second word to pair with the head.
        "Improved [Email] deliverability by tuning DKIM",
        "Rebuilt the [Address] validation service",
        "Migrated the [Date] parsing library to use ISO 8601",
        "Earned a [Degree] in Computer Science",
        # Bracketed institution/place names.
        "[Ohio State University]",
        "[Penn State]",
        "BSc, [Ohio State University], 2014",
        # "insert" as an ordinary database verb, no delimiter.
        "Optimised batch insert operations for the ingestion pipeline",
        "Reduced insert latency from 400ms to 12ms on the orders table",
        "Migrated bulk insert jobs to COPY for 8x throughput",
        # Lowercase, generic, or citation-style brackets.
        "Built [role-based access control] across 12 services",
        "Implemented [job queue] with Redis",
        "Shipped [Redis] and [Kafka] integrations",
        "Published in JMLR [1] and NeurIPS [2]",
        "Wrote the [title] parser for citations",
        # Proper nouns/phrases that happen to contain a field word, but not
        # as the head.
        "[Grant Date Analytics]",
        "[Phone Home]",
        "Led the [Client Onboarding] workstream",
        "Owned [Data Model] design for 3 clients",
        "Integrated [Salesforce] and [DealCloud]",
        "Reported to [VP Engineering]",
        "Handled [PII] redaction across the pipeline",
        "Used [ETL] tooling for the migration",
        "Client [A] and Client [B] integrations",
    ]
    for text in must_not_fire:
        assert find_placeholders(text) == [], text


def test_find_placeholders_catches_real_placeholders():
    must_fire = [
        "[Company Name]",
        "[Position Title]",
        "[Your Email]",
        "[Your Name]",
        "[Job Title]",
        "[FULL NAME]",
        "[YOUR NAME]",
        "[Employer Name]",
        "[School Name]",
        "[University Name]",
        "[Email Address]",
        "[Phone Number]",
        "[Contact Number]",
        "[Candidate Name]",
        "[Company_Name]",
        "[Insert metric here]",
        "{INSERT COMPANY NAME}",
        "<CANDIDATE_NAME>",
        "{{name}}",
        "Lorem ipsum dolor",
        "XX%",
        "[Your Role]",
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
