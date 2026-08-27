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
