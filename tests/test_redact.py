from screen.redact import assert_clean, redact

CANDIDATE = {
    "first_name": "Alex",
    "last_name": "Morgan",
    "email": "alex.morgan@example.com",
    "phone": "+1 415 555 0134",
}

MD = """# Alex Morgan
alex.morgan@example.com | +1 415 555 0134 | Boston, MA
linkedin.com/in/alexmorgan | github.com/alexmorgan

## Experience
### Staff Engineer, Northwind Data (2019-03 - Present)
- Led SSO rollout for 18 clients, cutting onboarding from 6 weeks to 9 days.

## Education
BSc Computer Science, State University
"""


def test_redacts_name_email_and_phone():
    r = redact(MD, CANDIDATE)
    assert "Alex Morgan" not in r.text
    assert "alex.morgan@example.com" not in r.text
    assert "555 0134" not in r.text
    assert "[NAME]" in r.text
    assert "[EMAIL]" in r.text
    assert "[PHONE]" in r.text


def test_redacts_linkedin_and_school_but_keeps_degree():
    r = redact(MD, CANDIDATE)
    assert "linkedin.com/in/alexmorgan" not in r.text
    assert "State University" not in r.text
    assert "BSc Computer Science" in r.text


def test_keeps_employer_names_and_achievements():
    # Employers and outcomes are exactly what the judge must score on.
    r = redact(MD, CANDIDATE)
    assert "Northwind Data" in r.text
    assert "cutting onboarding from 6 weeks to 9 days" in r.text
    assert "2019-03" in r.text


def test_redacts_us_city_state():
    r = redact(MD, CANDIDATE)
    assert "Boston, MA" not in r.text
    assert "[LOCATION]" in r.text


def test_tokens_replaced_counts_and_kinds():
    r = redact(MD, CANDIDATE)
    assert r.tokens_replaced >= 5
    assert r.kinds["name"] >= 1
    assert r.kinds["email"] >= 1


def test_assert_clean_finds_nothing_in_redacted_output():
    r = redact(MD, CANDIDATE)
    assert assert_clean(r.text) == []


def test_assert_clean_reports_leaks():
    leaks = assert_clean("Contact me at bob@example.com or +1 415 555 9999")
    assert any("email" in leak for leak in leaks)
    assert any("phone" in leak for leak in leaks)


def test_redact_handles_missing_candidate_fields():
    r = redact("Some CV text with no PII patterns.", {})
    assert r.text.strip() != ""
    assert r.tokens_replaced == 0


def test_redact_is_idempotent():
    once = redact(MD, CANDIDATE).text
    twice = redact(once, CANDIDATE).text
    assert once == twice


def test_email_survives_being_built_from_the_candidates_own_name():
    # Regression: name substitution ran before email substitution and chewed
    # "alex.morgan@example.com" into "[NAME].[NAME]@example.com" before the
    # email regex ever saw it, so no [EMAIL] token was ever produced.
    r = redact(MD, CANDIDATE)
    assert "[EMAIL]" in r.text
    assert r.kinds.get("email", 0) >= 1


def test_keeps_employer_name_that_contains_the_surname():
    candidate = {"first_name": "Sam", "last_name": "Bell", "email": "", "phone": ""}
    md = "# Sam Bell\n\n## Experience\n### Research Scientist, Bell Labs (2018 - 2021)\n- Published 4 papers.\n"
    r = redact(md, candidate)
    assert "Sam Bell" not in r.text
    assert "Bell Labs" in r.text


def test_does_not_redact_common_word_matching_surname():
    candidate = {"first_name": "Taylor", "last_name": "Grant", "email": "", "phone": ""}
    md = "# Taylor Grant\n\n## Experience\n- Led a grant reporting project for state agencies.\n"
    r = redact(md, candidate)
    assert "Taylor Grant" not in r.text
    assert "led a grant reporting project" in r.text.lower()


def test_preserves_latency_metrics_that_look_like_phone_numbers():
    r = redact("Reduced p99 latency from 1,250ms to 340ms.", {})
    assert "1,250ms" in r.text
    assert "340ms" in r.text


def test_redacts_first_middle_last_header_to_single_token():
    candidate = {"first_name": "Jordan", "last_name": "Vance", "email": "", "phone": ""}
    md = (
        "# Jordan Alexander Vance\n"
        "jordan.vance@example.com\n\n"
        "## Experience\n"
        "- Product Manager, Initech (2020 - 2023)\n"
    )
    r = redact(md, candidate)
    assert "Jordan Alexander Vance" not in r.text
    assert "Jordan" not in r.text
    assert "Vance" not in r.text
    assert r.text.count("[NAME]") == 1


def test_redacts_all_caps_header_name():
    candidate = {"first_name": "Priya", "last_name": "Nair", "email": "", "phone": ""}
    md = (
        "# PRIYA NAIR\n"
        "priya.nair@example.com\n\n"
        "## Experience\n"
        "- Data Analyst, Fabrikam (2021 - 2023)\n"
    )
    r = redact(md, candidate)
    assert "PRIYA" not in r.text
    assert "NAIR" not in r.text
    assert "[NAME]" in r.text


def test_redacts_surname_when_header_shows_different_given_name():
    # Mirrors a real shape: the ATS record's first name doesn't appear on the
    # CV at all (a preferred/married/nickname mismatch), so only the surname
    # can be matched — and it sits right next to a capitalised given name that
    # the guarded standalone matcher would normally treat as "adjacent
    # capital, skip".
    candidate = {"first_name": "Michael", "last_name": "Delgado", "email": "", "phone": ""}
    md = (
        "# Miguel Delgado\n"
        "miguel.delgado@example.com\n\n"
        "## Experience\n"
        "- Operations Lead, Fenwick Group (2019 - 2022)\n"
    )
    r = redact(md, candidate)
    assert "Delgado" not in r.text
    assert "[NAME]" in r.text


def test_over_redaction_guard_holds_outside_header():
    candidate = {"first_name": "Bell", "last_name": "Grant", "email": "", "phone": ""}
    md = (
        "# Bell Grant\n"
        "bell.grant@example.com\n\n"
        "## Experience\n"
        "- Senior Consultant, Bell Labs (2015 - 2018)\n"
        "- Advisor, Morgan Stanley (2018 - 2020)\n"
        "- Led a grant reporting initiative for nonprofit clients.\n"
        "- Engagement partner, Grant Thornton (2020 - Present)\n"
    )
    r = redact(md, candidate)
    assert "Bell Grant" not in r.text
    assert "[NAME]" in r.text
    assert "Bell Labs" in r.text
    assert "Morgan Stanley" in r.text
    assert "grant reporting" in r.text.lower()
    assert "Grant Thornton" in r.text


def test_header_window_stops_at_first_bullet():
    # Without the bullet/heading stop, a short CV's bullets fall inside the
    # unguarded header window and an employer name sharing the surname gets
    # eaten too.
    candidate = {"first_name": "Jamie", "last_name": "Ortiz", "email": "", "phone": ""}
    md = (
        "# Jamie Ortiz\n"
        "jamie.ortiz@example.com\n"
        "- Senior Analyst, Ortiz Data Partners (2019 - 2022)\n"
        "- Grew regional revenue by 22%.\n"
    )
    r = redact(md, candidate)
    assert "Jamie Ortiz" not in r.text
    assert "Ortiz Data Partners" in r.text


def test_redact_is_idempotent_for_middle_name_header():
    candidate = {"first_name": "Jordan", "last_name": "Vance", "email": "", "phone": ""}
    md = (
        "# Jordan Alexander Vance\n"
        "jordan.vance@example.com\n\n"
        "## Experience\n"
        "- Product Manager, Initech (2020 - 2023)\n"
    )
    once = redact(md, candidate).text
    twice = redact(once, candidate).text
    assert once == twice
