from fpl_advisor.advise import _whole_pct
from fpl_advisor.winprob import _pct


def test_whole_pct_matches_the_sentence():
    # The site shows round(p_first * 100) with JavaScript rounding; it must equal the sentence.
    for p in (0.205, 0.2049, 0.2051, 0.115, 0.5, 0.025, 0.875):
        js = int(_whole_pct(p) * 100 + 0.5)
        assert _pct(p) == f"about {js}%"


def test_edges_keep_detail_for_under_and_over():
    assert 0 < _whole_pct(0.004) < 0.01
    assert 0.99 < _whole_pct(0.996) < 1
    assert _whole_pct(0.0) == 0.0
