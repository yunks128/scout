from scout.pipeline.lexical_gate import score


def test_genesis_style_passes_high():
    r = score(
        title="Genesis Mission: Foundation model for grid resilience",
        description="DOE Office of Science ARPA-E",
        naics="541715",
        psc="AN",
    )
    assert r.passes
    assert r.score >= 10
    assert "GENESIS" in r.matches or "Genesis Mission" in r.matches


def test_unrelated_healthcare_fails():
    r = score(
        title="Healthcare workforce training grant",
        description="pharmaceutical education initiative",
    )
    assert not r.passes


def test_weak_match_fails():
    r = score(
        title="Generic R&D opportunity",
        description="some research",
    )
    assert not r.passes
    assert r.score < 3


def test_naics_boost():
    r = score(
        title="Electric power research",
        description="",
        naics="221121",
    )
    assert "NAICS:221121" in r.matches
