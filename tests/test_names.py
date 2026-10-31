from conftest import make_world

from fpl_advisor.names import norm, player_id, team_id
from fpl_advisor.world import Team


def test_norm_handles_letters_without_decomposition():
    assert norm("Martin Ødegaard") == "martin odegaard"
    assert norm("Gabriel Magalhães") == "gabriel magalhaes"
    assert norm("Łukasz Fabiański") == "lukasz fabianski"


def test_team_full_name_wins_over_alias():
    w = make_world()
    w.teams[1] = Team(1, "Ipswich Town", "IPS")
    w.teams[2] = Team(2, "Man Utd", "MUN")
    assert team_id(w, "Ipswich Town") == 1
    assert team_id(w, "Manchester United") == 2
    assert team_id(w, "Nowhere FC") is None


def test_player_by_full_name_and_ambiguity():
    w = make_world()
    p = next(iter(w.players.values()))
    assert player_id(w, p.full_name) == p.id
    assert player_id(w, "") is None
