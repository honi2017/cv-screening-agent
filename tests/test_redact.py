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
