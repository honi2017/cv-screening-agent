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


# --- Fix verification: ATS field-name matching must be an exact allowlist -
#
# The candidate's residence drives gate G5, a hard elimination, so the
# location field is identified by an EXACT label match -- never by needle
# matching. Both substring and whole-word matching were tried in turn and
# both let unrelated fields masquerade as the location: substring matching
# let "city" match inside "Ethnicity" and "location" match inside
# "Relocation" (Preference); whole-word matching narrowed that but still let
# "Interview Location", "Office Location", "Previous Address", and "Email
# Address" match, because each one legitimately CONTAINS a location word for
# a reason that has nothing to do with the candidate's residence. The
# Ethnicity case is the most serious: the spec explicitly forbids weighing
# nationality or ethnicity anywhere, and matching that field bypassed that
# in the deterministic layer entirely. The Email Address case is the
# clearest sign a name-matching approach was structurally wrong: an email
# domain being read as a country of residence. Each of these silently
# shadowed a co-present real `Location` field and eliminated the candidate.
#
# An unrecognised label is the SAFE failure: `_profile_value` returns None,
# which falls back to the CV's contact header and then to unknown location
# -- and unknown location flags rather than eliminates. If a future change
# widens `_LOCATION_LABELS` matching back into a needle or substring check
# to "catch more label phrasings", it reopens every one of these bugs --
# don't.


def test_profile_value_ignores_ethnicity_field_uses_real_location():
    pd = [
        {"name": "Ethnicity", "value": "Vietnamese American"},
        {"name": "Location", "value": "Boston, MA"},
    ]
    r = find_location("no address in cv", pd)
    assert r["non_us_explicit"] is False
    assert r["raw"] == "Boston, MA"


def test_profile_value_ignores_relocation_preference_field_uses_real_location():
    pd = [
        {"name": "Relocation Preference", "value": "Open to Singapore office"},
        {"name": "Location", "value": "Austin, TX"},
    ]
    r = find_location("no address", pd)
    assert r["non_us_explicit"] is False
    assert r["raw"] == "Austin, TX"


def test_profile_value_ignores_nationality_field_uses_real_location():
    pd = [
        {"name": "Nationality", "value": "Vietnamese"},
        {"name": "Location", "value": "Chicago, IL"},
    ]
    r = find_location("no address", pd)
    assert r["non_us_explicit"] is False
    assert r["raw"] == "Chicago, IL"


def test_profile_value_whole_word_still_resolves_genuine_field_variants():
    # Pinning the change as not over-tightened: real location-ish field names
    # must still resolve.
    assert find_location("no address", [{"name": "Current City", "value": "Denver, CO"}])["raw"] == "Denver, CO"
    assert (
        find_location("no address", [{"name": "Home Address", "value": "123 Main St, Boston, MA"}])["raw"]
        == "123 Main St, Boston, MA"
    )


def test_profile_value_ignores_interview_location_field_uses_real_location():
    pd = [
        {"name": "Interview Location", "value": "Remote - Hanoi, Vietnam office"},
        {"name": "Location", "value": "Boston, MA"},
    ]
    r = find_location("no address in cv", pd)
    assert r["non_us_explicit"] is False
    assert r["raw"] == "Boston, MA"


def test_profile_value_ignores_office_location_field_uses_real_location():
    pd = [
        {"name": "Office Location", "value": "Singapore"},
        {"name": "Location", "value": "Boston, MA"},
    ]
    r = find_location("no address", pd)
    assert r["non_us_explicit"] is False
    assert r["raw"] == "Boston, MA"


def test_profile_value_ignores_previous_address_field_uses_real_location():
    pd = [
        {"name": "Previous Address", "value": "Hanoi, Vietnam"},
        {"name": "Location", "value": "Boston, MA"},
    ]
    r = find_location("no address", pd)
    assert r["non_us_explicit"] is False
    assert r["raw"] == "Boston, MA"


def test_profile_value_ignores_email_address_field_uses_real_location():
    # The clearest sign the needle/whole-word approach was structurally
    # wrong: an email domain read as a country of residence.
    pd = [
        {"name": "Email Address", "value": "jane@vietnamsoftware.com"},
        {"name": "Location", "value": "Boston, MA"},
    ]
    r = find_location("no address", pd)
    assert r["non_us_explicit"] is False
    assert r["raw"] == "Boston, MA"


def test_profile_value_allowlist_recognises_common_label_variants():
    # Pinning the allowlist as not over-tight: these common label phrasings
    # must still resolve to the real location value.
    assert (
        find_location("no address", [{"name": "Location (City, State)", "value": "Austin, TX"}])["raw"]
        == "Austin, TX"
    )
    assert find_location("no address", [{"name": "City/Town", "value": "Chicago, IL"}])["raw"] == "Chicago, IL"
    assert find_location("no address", [{"name": "Country", "value": "Vietnam"}])["raw"] == "Vietnam"


def test_profile_value_unrecognised_label_degrades_to_unknown_not_elimination():
    # An unrecognised label is the SAFE failure: the field is ignored
    # entirely (even though its value looks like a plausible location),
    # `raw` falls back to the CV's contact header, and an unrecognised
    # location degrades to "unknown" -- which flags, never eliminates.
    pd = [{"name": "Where are you based?", "value": "Boston, MA"}]
    r = find_location("no address in cv", pd)
    assert r["raw"] is None
    assert r["non_us_explicit"] is False


# --- Fix verification: bare "US"/"U.S."/"U.S.A." must be recognised -------
#
# A dual national who writes "Citizen of the US and Ireland" was eliminated
# while the identical sentence spelled "United States" was safe -- a wording
# accident, not a real residence signal. Case sensitivity is deliberate: the
# pattern must not match the lowercase pronoun "us" ("joined us in 2019").


def test_us_hint_recognises_bare_us_abbreviation_dual_national_safe():
    r = find_location("Citizen of the US and Ireland", [])
    assert r["us_evident"] is True
    assert r["non_us_explicit"] is False


def test_us_hint_does_not_match_lowercase_pronoun_us():
    assert find_location("joined us in 2019", [])["us_evident"] is False
    assert find_location("USB debugging experience", [])["us_evident"] is False


# --- Declined finding, pinned as a known, deliberately-left false positive -
#
# find_degree credits an MSc for the bare word "master", so "Certified Scrum
# Master" or "Master Service Agreements" can register as a master's degree.
# Left deliberately: find_degree scopes to the Education section when one
# exists, so a realistic CV with a real Education section is not fooled by
# "Scrum Master" appearing elsewhere in the document; the false positive only
# reaches a CV with no detectable Education section at all. The failure
# direction is lenient (wrongly *crediting* a degree, so no -5 penalty lands
# on anyone), and tightening the "master" match to require "master's"/"master
# of X" would produce the harmful inverse: "Master, Computer Science, 2016"
# would lose its real degree and take the penalty. This test pins the safe
# case so nobody "fixes" this deliberate trade-off without re-litigating it.


def test_find_degree_scrum_master_does_not_shadow_real_education_section():
    md = """# Jane Doe

## Summary
Certified Scrum Master with a track record of shipping on time.

## Education
BSc Computer Science, State University, 2016

## Experience
- Certified Scrum Master, led agile ceremonies
- Negotiated Master Service Agreements with vendors
"""
    r = find_degree(md)
    assert r["level"] == "BSc"


# --- Fix verification: an ATS Location value must be SHAPED like a place --
#
# The exact-label allowlist (above) closed every case of the WRONG field
# being read as a location. It did not close the case of the RIGHT field
# holding the wrong kind of value: gate G5 is a hard elimination, and a
# correctly-labelled `Location` field was still eliminating candidates
# whose value was free text ("Interested in opportunities across Singapore
# and Vietnam"), an explicit relocation OFFER ("Willing to relocate to our
# Singapore office" -- the giveaway that a bare substring scan for a country
# name, or even for the word "relocate", cannot tell a statement of intent
# from a statement of residence), or a bare domain being read as a country
# ("vietnamsoftware.com"). `_is_residence_evidence` requires the value to
# end with the place in a short trailing component, the way a real address
# does, and rejects intent language and domains outright. If a future
# change reverts `find_location`'s ATS-field path to a bare substring scan
# for a country name (the same pattern this replaced twice already), all of
# these reopen.


def test_is_residence_evidence_recognises_real_addresses():
    # These must all continue to set non_us_explicit=True: genuine
    # residence-shaped values, in various forms this rule must keep passing.
    residence_values = [
        "Hanoi, Vietnam",
        "Vietnam",
        "Ho Chi Minh City, Vietnam",
        "Bangalore, India",
        "Berlin, Germany",
        "London, United Kingdom",
        "Toronto, Canada",
        "Dublin, Ireland",
        "Vietnam.",
        "Da Nang City, Viet Nam",
    ]
    for value in residence_values:
        pd = [{"name": "Location", "value": value}]
        r = find_location("no address in cv", pd)
        assert r["non_us_explicit"] is True, f"{value!r} should count as residence evidence"


def test_is_residence_evidence_rejects_prose_intent_and_domains():
    # None of these may ever set non_us_explicit=True, even though most
    # contain a non-US country name or the word "relocate": prose about
    # offshore work, an explicit relocation OFFER, a bare domain, an email
    # address, and genuine US addresses (sanity check: the rule must not
    # start rejecting real US "City, ST" values either).
    non_residence_values = [
        "Interested in opportunities across Singapore and Vietnam",
        "Willing to relocate to our Singapore office",
        "vietnamsoftware.com",
        "jane@vietnamsoftware.com",
        "Boston, MA",
        "Austin, TX",
        "Chicago, IL",
        "Remote - Hanoi, Vietnam office",
        "Open to Singapore",
        "US citizen currently in Germany",
        "Seeking roles in Germany",
        "Worked extensively with teams in India and Vietnam",
    ]
    for value in non_residence_values:
        pd = [{"name": "Location", "value": value}]
        r = find_location("no address in cv", pd)
        assert r["non_us_explicit"] is False, f"{value!r} must NOT count as residence evidence"
