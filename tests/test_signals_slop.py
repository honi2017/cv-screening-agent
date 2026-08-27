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
    # UPDATED (see task-4-report.md, Fix 4): [Company Name] and [Position
    # Title] are no longer caught. A two-word bracketed field label like
    # these is grammatically identical to a legitimate domain object an
    # engineer would cite ("[Tracking Number]", "[IP Address]"), so
    # distinguishing them needs the surrounding sentence and is deliberately
    # left to the judge -- see
    # test_find_placeholders_documents_deliberate_false_negatives. Only
    # "[Your Email]" is unambiguous (second person) and still fires here.
    text = "Excited to join [Company Name] as a [Position Title]. Contact [Your Email]."
    kinds = [p["match"] for p in find_placeholders(text)]
    assert "[Your Email]" in kinds
    assert "[Company Name]" not in kinds
    assert "[Position Title]" not in kinds


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
    # unappealable rejection of a qualified applicant. Five earlier designs
    # of this detector were each defeated by one of these classes in turn:
    #   - "insert" as a bare database verb ("batch insert operations")
    #   - lowercase generic brackets ("[role-based access control]", "[job queue]")
    #   - bracketed institution/place names containing a field-ish word
    #     ("[Ohio State University]", "[Penn State]")
    #   - NDA-anonymised employers, which put the field noun FIRST, not last
    #     ("[Company A]", "[Employer Redacted]", "[Candidate Matching]")
    #   - single-word domain labels, which have no second word at all
    #     ("[Email]", "[Address]", "[Date]", "[Degree]", "[PII]", "[ETL]")
    #   - two-word bracketed DOMAIN objects that are grammatically identical
    #     to a template label ("[Tracking Number]", "[IP Address]") -- this
    #     is why the detector no longer has ANY generic bracket-label
    #     pattern; see test_find_placeholders_documents_deliberate_false_negatives
    #   - ALL_CAPS env-var tokens in angle brackets ("<DB_NAME>", "<API_KEY>",
    #     "<SERVICE_NAME>") -- a field-word-inside-the-delimiter check keeps
    #     failing the same way regardless of the delimiter shape, so there is
    #     no ALL_CAPS-token pattern left either
    # The current design only fires on markers that CANNOT be legitimate CV
    # content: "[Your ...]" (second person), "insert" inside an explicit
    # delimiter, lorem ipsum, XX%/NN%, and {{mustache}}. That's it -- five
    # patterns, no bracket-label or ALL_CAPS-token pattern of any kind.
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
        # Two-word bracketed domain/field references -- structurally
        # identical to a template label but naming a real system field.
        "Built [Tracking Number] validation for the shipping service",
        "Wrote the [Serial Number] parser for warranty claims",
        "Owned [Case Number] routing for the support platform",
        "Migrated [Account Number] masking across the platform",
        "Built [Purchase Order Number] validation for procurement",
        "Owned [Policy Number] lookups for the claims platform",
        "Normalised [IP Address] geolocation lookups",
        "Tracked the [Release Date] field",
        # ALL_CAPS angle-bracket tokens: env vars are the same shape as an
        # applicant-field token, and this pool cites <DB_NAME> far more often
        # than it leaves behind <CANDIDATE_NAME> -- so the ALL_CAPS pattern
        # was deleted entirely rather than narrowed a sixth time.
        "Documented <DATABASE_URL> and <API_KEY> in the onboarding runbook",
        "Configured <REDIS_HOST> for staging",
        "Configured <DB_NAME> and <DB_HOST> for the migration",
        "Set the <TABLE_NAME> env var for the ETL job",
        "Documented <SERVICE_NAME> routing in the runbook",
        "Set <USER_EMAIL> in the notification template",
        "Rotated <COMPANY_API_KEY> quarterly",
        "Tuned <JOB_QUEUE_NAME> throughput",
        "Built <FULL_TEXT_INDEX> search",
    ]
    for text in must_not_fire:
        assert find_placeholders(text) == [], text


def test_find_placeholders_catches_real_placeholders():
    # Only markers that cannot be legitimate CV content: second-person
    # brackets, delimited "insert" instructions, mustache tags, lorem ipsum,
    # and unfilled XX%/NN% metrics. No ALL_CAPS angle-bracket pattern exists
    # any more (see test_find_placeholders_documents_deliberate_false_negatives) --
    # <CANDIDATE_NAME>/<COMPANY_NAME>/<FULL_NAME> no longer fire by design.
    must_fire = [
        "[Your Name]",
        "[Your Email]",
        "[Your Role]",
        "[YOUR NAME]",
        "[your company]",
        "[Insert metric here]",
        "{INSERT COMPANY NAME}",
        "{{name}}",
        "{{company}}",
        "Lorem ipsum dolor sit amet",
        "Improved throughput by XX%",
        "Reduced cost by NN%",
    ]
    for text in must_fire:
        assert find_placeholders(text) != [], text


def test_find_placeholders_documents_deliberate_false_negatives():
    # These ARE genuine, unfilled template labels -- both the bracketed
    # ("[Company Name]") and the ALL_CAPS angle-bracket ("<CANDIDATE_NAME>")
    # families. But both shapes are indistinguishable, mechanically, from
    # legitimate CV content an engineer would write: a bracketed field label
    # is grammatically identical to a domain object ("[Tracking Number]",
    # "[Case Number]", "[IP Address]"), and an ALL_CAPS angle-bracket token is
    # identical in shape to an env-var reference ("<DB_NAME>", "<API_KEY>") --
    # which this candidate pool cites far more often than it leaves behind an
    # unfilled template token. Telling either apart from real content needs
    # the surrounding sentence, which is a judgement call left to the RedFlag
    # judge pass, not this mechanical detector. Five successive regex designs
    # were each defeated by a realistic phrase before this boundary was
    # deliberately drawn here (see task-4-report.md) -- a failure in this
    # test is not a bug to fix by reintroducing a generic bracket-label or
    # ALL_CAPS-token pattern.
    must_not_fire = [
        "[Company Name]",
        "[Position Title]",
        "[Phone Number]",
        "[Email Address]",
        "[Job Title]",
        "[FULL NAME]",
        "[Employer Name]",
        "[School Name]",
        "[University Name]",
        "[Contact Number]",
        "[Candidate Name]",
        "[Company_Name]",
        "<CANDIDATE_NAME>",
        "<COMPANY_NAME>",
    ]
    for text in must_not_fire:
        assert find_placeholders(text) == [], text


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
