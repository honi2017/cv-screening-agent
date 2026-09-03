"""screen.signals.find_offshore_claims -- reference-flag detection only.

See the long comment above that function (and above screen.rank._reference_flags)
for why this is deliberately loose: it feeds a flag that can never cost a
candidate a point or a rank, so recall matters far more than precision here.
"""

from screen.signals import find_offshore_claims


def test_fires_on_bullet_naming_offshore_team_and_country():
    md = (
        "## Experience\n"
        "- Collaborated with offshore engineering teams in Vietnam to design "
        "and deploy integration APIs.\n"
        "- Led migration to a microservices architecture.\n"
    )
    claims = find_offshore_claims(md)
    assert len(claims) == 1
    assert "Vietnam" in claims[0]["sentence"]
    assert "vietnam" in claims[0]["places"]


def test_does_not_fire_on_cv_with_no_such_claim():
    md = (
        "## Experience\n"
        "- Led migration to a microservices architecture.\n"
        "- Mentored two junior engineers on the platform team.\n"
        "## Skills\n"
        "Python, Kubernetes, PostgreSQL\n"
    )
    assert find_offshore_claims(md) == []


def test_verbatim_sentence_is_preserved_exactly():
    sentence = "Partnered daily with our nearshore team based in Poland on the billing platform."
    md = f"## Experience\n- {sentence}\n"
    claims = find_offshore_claims(md)
    assert len(claims) == 1
    assert claims[0]["sentence"] == sentence


def test_fires_on_country_plus_collaboration_word_without_offshore_keyword():
    # No "offshore"/"nearshore"/"offshored" term at all -- fires only because
    # a country name sits alongside a collaboration word, per the looser
    # second branch of the rule.
    sentence = "Coordinated release planning with engineers in India each sprint."
    md = f"## Experience\n- {sentence}\n"
    claims = find_offshore_claims(md)
    assert len(claims) == 1
    assert "india" in claims[0]["places"]


def test_country_name_alone_does_not_fire_without_a_collaboration_word():
    # Names a country but no collaboration word nearby, and no offshore
    # keyword -- must not fire (this is describing travel, not a team).
    md = "## Summary\n- Relocated from Vietnam to pursue new opportunities.\n"
    assert find_offshore_claims(md) == []


def test_dedupes_identical_sentence_encountered_twice():
    sentence = "Collaborated with an offshore QA team throughout the release cycle."
    md = f"## Experience\n- {sentence}\n## Projects\n- {sentence}\n"
    claims = find_offshore_claims(md)
    assert len(claims) == 1
