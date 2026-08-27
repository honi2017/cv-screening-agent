from screen.signals import find_degree, find_linkedin, find_location


def test_find_linkedin_prefers_trakstar_profile_data():
    pd = [{"name": "LinkedIn Profile", "value": "https://linkedin.com/in/alexmorgan"}]
    r = find_linkedin("no link in cv", pd, "Alex Morgan")
    assert r["present"] is True
    assert r["source"] == "trakstar"
    assert r["name_matches"] is True


def test_find_linkedin_falls_back_to_cv_text():
    r = find_linkedin("linkedin.com/in/alex-morgan", [], "Alex Morgan")
    assert r["present"] is True
    assert r["source"] == "cv"
    assert r["name_matches"] is True


def test_find_linkedin_absent_everywhere():
    r = find_linkedin("no profile here", [], "Alex Morgan")
    assert r == {"present": False, "source": "none", "url": None, "name_matches": None}


def test_find_linkedin_name_mismatch_flagged():
    pd = [{"name": "LinkedIn", "value": "https://linkedin.com/in/someoneelse123"}]
    r = find_linkedin("", pd, "Alex Morgan")
    assert r["present"] is True
    assert r["name_matches"] is False


def test_find_linkedin_name_unknown_for_opaque_slug():
    pd = [{"name": "LinkedIn", "value": "https://linkedin.com/in/ab12xy90"}]
    r = find_linkedin("", pd, "Alex Morgan")
    assert r["name_matches"] is None


def test_find_degree_detects_level_and_field():
    md = "## Education\nBSc Computer Science, State University\n"
    r = find_degree(md)
    assert r["present"] is True
    assert r["level"] == "BSc"
    assert "computer science" in r["field"].lower()


def test_find_degree_detects_spelled_out_bachelors_and_masters():
    assert find_degree("Bachelor of Science in Information Systems")["present"] is True
    assert find_degree("Master's degree in Computer Engineering")["level"] in {"MSc", "Master"}


def test_find_degree_absent():
    r = find_degree("## Experience\n- worked places\n")
    assert r == {"present": False, "level": None, "field": None}


def test_find_location_us_state_gives_timezone_hint():
    r = find_location("Boston, MA", [])
    assert r["us_evident"] is True
    assert r["non_us_explicit"] is False
    assert r["timezone_hint"] == "ET"


def test_find_location_central_state():
    assert find_location("Austin, TX", [])["timezone_hint"] == "CT"


def test_find_location_non_us_explicit():
    r = find_location("Hanoi, Vietnam", [])
    assert r["non_us_explicit"] is True
    assert r["us_evident"] is False


def test_find_location_unknown_when_silent():
    r = find_location("no address on this cv", [])
    assert r["us_evident"] is False
    assert r["non_us_explicit"] is False
    assert r["timezone_hint"] == "unknown"


def test_find_location_prefers_trakstar_field():
    pd = [{"name": "Location", "value": "Chicago, IL"}]
    r = find_location("no address", pd)
    assert r["us_evident"] is True
    assert r["timezone_hint"] == "CT"


# --- Regression coverage for defects found by probing beyond the brief -----
#
# The brief's own given tests all pass against the brief's own given
# implementation of _slug_matches_name and _DEGREE_PATTERNS EXCEPT one:
# test_find_linkedin_prefers_trakstar_profile_data (above) fails against the
# verbatim brief code, because the slug "alexmorgan" never tokenizes without a
# separator. That was fixed in-place; these two tests additionally lock in
# that fix and a second defect (bare "BS"/"MS" going undetected) found while
# probing, so neither regresses silently the way the brief warns two prior
# tasks' regex defects did.


def test_find_linkedin_name_matches_concatenated_vanity_slug():
    # linkedin.com/in/alexmorgan (no separator) is the single most common
    # vanity-URL shape and must not be penalised as a mismatch.
    pd = [{"name": "LinkedIn", "value": "https://linkedin.com/in/alexmorgan"}]
    assert find_linkedin("", pd, "Alex Morgan")["name_matches"] is True
    pd_rev = [{"name": "LinkedIn", "value": "https://linkedin.com/in/morganalex"}]
    assert find_linkedin("", pd_rev, "Alex Morgan")["name_matches"] is True


def test_find_degree_detects_bare_bs_and_ms_without_periods():
    # "BS"/"MS" with no periods are at least as common on real resumes as
    # "BSc"/"MSc" but were previously invisible to _DEGREE_PATTERNS, wrongly
    # zeroing out `present` (and the -5pt penalty) for a real degree holder.
    assert find_degree("BS Computer Science")["present"] is True
    assert find_degree("MS in Computer Science")["present"] is True


# --- Fix verification: G5 elimination boundary (find_location) ------------
#
# Country names in prose describe WORK, not residence -- and this role's JD
# explicitly rewards offshore collaboration ("engineering and product teams
# based in Vietnam"), so scanning prose for country names was eliminating
# exactly the candidates the JD most wants (confirmed: a US-based candidate
# whose Summary read "clients across Vietnam and Singapore" was coming back
# non_us_explicit=True with nothing to cancel it). `non_us_explicit` must
# require positive evidence of RESIDENCE -- the ATS location field, or the
# CV's contact header -- never a country name appearing anywhere in the
# document. If a future change "improves" this detector by widening the
# residence scan back to the full first-12-lines window (or the whole
# document), it reopens this elimination bug -- don't do that.


def test_find_location_us_candidate_offshore_summary_city_below_header():
    md = """# Jane Doe
jane.doe@example.com | 555-123-4567

## Summary
Senior engineer with 10 years leading cross-functional teams. Delivered
enterprise integrations for clients across Vietnam and Singapore, and
partnered with engineering teams based in Germany on platform migrations.
Strong background in distributed systems and offshore collaboration.

## Education
BSc Computer Science, Technical University of Munich, Germany

## Experience
- Senior Engineer, Acme Corp, Boston, MA (2019-Present)
"""
    r = find_location(md, [])
    assert r["non_us_explicit"] is False
    assert r["timezone_hint"] == "ET"


def test_find_location_us_candidate_foreign_university():
    md = """# Jane Doe
jane.doe@example.com

## Education
BSc Computer Science, Technical University of Munich, Germany

## Experience
- Senior Engineer, Acme Corp (2019-Present)
"""
    r = find_location(md, [])
    assert r["non_us_explicit"] is False
    assert r["timezone_hint"] == "unknown"


def test_find_location_us_city_in_header_vietnam_in_summary():
    md = """# Jane Doe
Chicago, IL | jane.doe@example.com

## Summary
Delivered integrations for clients across Vietnam and Singapore.

## Experience
- Senior Engineer, Acme Corp (2019-Present)
"""
    r = find_location(md, [])
    assert r["non_us_explicit"] is False
    assert r["timezone_hint"] == "CT"


def test_find_location_non_us_header_eliminates():
    md = """# Jane Doe
Hanoi, Vietnam | jane.doe@example.com

## Experience
- Senior Engineer, Acme Corp (2019-Present)
"""
    r = find_location(md, [])
    assert r["non_us_explicit"] is True
    assert r["timezone_hint"] == "unknown"


def test_find_location_non_us_header_with_work_auth_is_safe():
    md = """# Jane Doe
Hanoi, Vietnam | jane.doe@example.com

## Experience
- Senior Engineer, Acme Corp (2019-Present)

Authorized to work in the United States.
"""
    r = find_location(md, [])
    assert r["non_us_explicit"] is False


def test_find_location_ats_field_non_us_eliminates():
    pd = [{"name": "Location", "value": "Hanoi, Vietnam"}]
    r = find_location("no address in cv body", pd)
    assert r["non_us_explicit"] is True


def test_find_location_ats_field_us_state_is_safe():
    pd = [{"name": "Location", "value": "Chicago, IL"}]
    r = find_location("no address", pd)
    assert r["non_us_explicit"] is False
    assert r["timezone_hint"] == "CT"


# --- Fix verification: state codes require the "City, ST" shape -----------
#
# A bare two-letter uppercase token is common CV/degree vocabulary ("BS",
# "MS") as well as a state code, and Mississippi/Oregon collide with exactly
# that vocabulary. Requiring a preceding comma keeps every genuine "City, ST"
# address shape while no longer matching a degree abbreviation or acronym.


def test_us_hint_requires_city_comma_state_shape():
    cases = [
        ("BS Computer Science", "us_evident", False),
        ("MS in Data Science", "us_evident", False),
        ("Boston, MA", "timezone_hint", "ET"),
        ("Denver, CO", "timezone_hint", "MT"),
        ("Portland, OR", "timezone_hint", "PT"),
        ("Jackson, MS", "timezone_hint", "CT"),
        ("United States", "us_evident", True),
        ("Austin, TX 78701", "timezone_hint", "CT"),
    ]
    for text, key, expected in cases:
        r = find_location(text, [])
        assert r[key] == expected, f"{text!r}: expected {key}={expected}, got {r[key]!r}"
