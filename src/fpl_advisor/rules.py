"""FPL game rules and scoring constants (2025/26 rules onward). Update here when FPL changes them."""

GKP, DEF, MID, FWD = 1, 2, 3, 4
POS_NAME = {GKP: "GKP", DEF: "DEF", MID: "MID", FWD: "FWD"}

SQUAD_SIZE = 15
SQUAD_COUNT = {GKP: 2, DEF: 5, MID: 5, FWD: 3}
XI_MIN = {GKP: 1, DEF: 3, MID: 2, FWD: 1}
XI_MAX = {GKP: 1, DEF: 5, MID: 5, FWD: 3}
MAX_PER_CLUB = 3
HIT_COST = 4
MAX_FREE_TRANSFERS = 5

GOAL_PTS = {GKP: 10, DEF: 6, MID: 5, FWD: 4}
ASSIST_PTS = 3
CLEAN_SHEET_PTS = {GKP: 4, DEF: 4, MID: 1, FWD: 0}
APPEARANCE_PTS_60 = 2
APPEARANCE_PTS_SUB60 = 1
SAVES_PER_PT = 3
GOALS_CONCEDED_PER_MINUS = 2  # GKP/DEF lose 1 point per 2 conceded
# Defensive contributions: 2 points for reaching the threshold in a match (GKP not eligible).
DC_THRESHOLD = {DEF: 10, MID: 12, FWD: 12}
DC_PTS = 2
YELLOW_PTS = -1

CHIP_NAMES = {
    "wildcard": "Wildcard",
    "freehit": "Free Hit",
    "bboost": "Bench Boost",
    "3xc": "Triple Captain",
}
