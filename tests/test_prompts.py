from pathlib import Path

from screen.config import load_role

CFG = load_role(Path(__file__).resolve().parents[1] / "roles" / "fde")


def test_redflag_prompt_has_placeholders():
    text = CFG.redflag_prompt()
    assert "{{REDACTED_CV}}" in text
    assert "{{PRECHECKS}}" in text


def test_redflag_prompt_defines_all_three_tiers():
    text = CFG.redflag_prompt().lower()
    for tier in ("tier 1", "tier 2", "tier 3"):
        assert tier in text


def test_redflag_prompt_requires_verbatim_quotes():
    text = CFG.redflag_prompt().lower()
    assert "verbatim" in text
    assert "quote" in text


def test_redflag_prompt_protects_polished_but_specific_writing():
    text = CFG.redflag_prompt().lower()
    assert "tier 3" in text
    assert "do not" in text


def test_redflag_prompt_does_not_claim_placeholders_are_mechanical_only():
    """The brief's original line ("placeholder text ... already detected
    mechanically -- do not re-report") is now false: the mechanical detector
    was deliberately narrowed to unambiguous markers only, and everything
    else (bracketed field labels, angle-bracket tokens) is the judge's job.
    """
    text = CFG.redflag_prompt().lower()
    assert "placeholder text and duplicated bullets are already detected mechanically" not in text


def test_redflag_prompt_tells_judge_to_flag_leftover_template_placeholders():
    text = CFG.redflag_prompt()
    assert "[Company Name]" in text or "[Position Title]" in text
    assert "<COMPANY_NAME>" in text or "<CANDIDATE_NAME>" in text


def test_redflag_prompt_protects_bracketed_domain_objects_and_anonymised_employers():
    text = CFG.redflag_prompt()
    assert "[Tracking Number]" in text or "[Case Number]" in text or "[IP Address]" in text
    assert "<DB_NAME>" in text or "<API_KEY>" in text
    assert "[Company A]" in text or "[Employer Redacted]" in text


def test_redflag_prompt_says_jd_mirroring_is_tier3_not_flagged():
    text = CFG.redflag_prompt().lower()
    assert "mirror" in text
    # The mirroring discussion must land in the "do not flag" / Tier 3
    # territory, not be silent about it.
    assert "tier 3" in text
    assert "must not be flagged" in text


def test_redflag_prompt_requires_skills_signal_to_be_long_list_mostly_unevidenced():
    text = CFG.redflag_prompt().lower()
    assert "skills without evidence" in text or "skills_without_evidence" in text
    assert "long" in text
    assert "most" in text


def test_redflag_prompt_requires_generic_summary_to_be_transferable():
    text = CFG.redflag_prompt().lower()
    assert "transfer" in text
    assert "different employer" in text
    assert "different role" in text


def test_redflag_prompt_tells_judge_not_to_invent_kind_values():
    text = CFG.redflag_prompt().lower()
    assert "do not invent new" in text
    assert "kind" in text


def test_redflag_prompt_requires_material_or_refuted_claim_for_summary_contradicts_body():
    text = CFG.redflag_prompt().lower()
    assert "summary contradicts the body" in text
    assert "years-of-experience or seniority claim" in text
    assert "named employer, title, credential, or qualification" in text
    assert "quantified claim" in text
    assert "refutes" in text or "refute" in text


def test_redflag_prompt_excludes_partial_domain_evidence_from_summary_contradicts_body():
    text = CFG.redflag_prompt().lower()
    assert "do not flag" in text
    assert "domain, industry, or technology" in text
    assert "normal framing, not a" in text


def test_redflag_prompt_says_summary_contradicts_body_is_unappealable_and_favors_not_flagging():
    text = CFG.redflag_prompt().lower()
    assert "no appeal" in text
    assert "resolve toward not flagging" in text


def test_fit_prompt_has_placeholders_including_redflag_result():
    text = CFG.fit_prompt()
    for token in ("{{REDACTED_CV}}", "{{PRECHECKS}}", "{{REDFLAG_RESULT}}"):
        assert token in text


def test_fit_prompt_lists_every_criterion_with_its_max():
    text = CFG.fit_prompt()
    for c in CFG.criteria:
        assert c.key in text
        assert str(c.max) in text


def test_fit_prompt_states_the_anchors():
    text = CFG.fit_prompt().lower()
    for phrase in ("no evidence", "one concrete", "sustained"):
        assert phrase in text


def test_fit_prompt_carries_the_fairness_rule():
    text = CFG.fit_prompt().lower()
    for banned in ("nationality", "gender", "age"):
        assert banned in text
    assert "prestige" in text or "institution" in text


def test_fit_prompt_caps_the_bonus():
    assert str(CFG.bonus_max) in CFG.fit_prompt()
    assert "{{BONUS_MAX}}" not in CFG.fit_prompt()


def test_both_prompts_demand_json_only():
    for text in (CFG.redflag_prompt(), CFG.fit_prompt()):
        lowered = text.lower()
        assert "json" in lowered
        assert "only" in lowered
