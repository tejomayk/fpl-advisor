// The shape of GET /api/advice (see src/fpl_advisor/serialize.py and static/sample_advice.json).
// Every field is treated as optional: the UI must survive partial or older payloads.

export type Pos = "GKP" | "DEF" | "MID" | "FWD";

export interface Player {
  id: number;
  name: string;
  team?: string;
  pos?: Pos | string;
  price?: number;
  sell_price?: number;
  xp_next?: number;
  xp_horizon?: number;
  fixtures?: string;
  next_fixture?: string;
  status?: string;
  chance?: number | null;
  news?: string;
  price_move?: "rise" | "fall" | null;
  team_news?: string | null;
  team_news_source?: string | null;
  p_start?: number | null;
  p_goal?: number | null;
  p_assist?: number | null;
  elite_own?: number | null;
  elite_captain?: number | null;
  elite_in?: number | null;
  elite_out?: number | null;
  in_your_team?: boolean;
  ownership?: number;
}

export interface Move {
  out?: Player;
  in?: Player;
  gain?: number;
  why_out?: string;
  why_in?: string;
}

export interface Transfers {
  headline?: string;
  initial?: boolean;
  free_transfers?: number;
  count?: number;
  hits?: number;
  hit_cost?: number;
  hit_note?: string | null;
  moves?: Move[];
  squad?: Player[];
  bank_after?: number;
}

export interface Sheet {
  xi: Player[];
  bench?: Player[];
  captain_id?: number;
  vice_id?: number;
  expected_points?: number;
  bench_boost?: boolean;
}

export interface Lineup extends Sheet {
  current?: Sheet | null;
}

export interface Chip {
  chip: string;
  name?: string;
  play_now?: boolean;
  expires_gw?: number | null;
  reason?: string;
}

export interface TableRow {
  rank: number;
  manager: string;
  team_name?: string;
  total?: number;
  event_total?: number;
  is_me?: boolean;
}

export interface League {
  name?: string;
  rank?: number;
  size?: number;
  total_managers?: number;
  tracked?: number;
  table?: TableRow[];
  gap_to_leader?: number;
  target?: number;
  required_per_gw?: number;
  my_average?: number;
  remaining_gws?: number;
  last_season_best?: { manager: string; points: number } | null;
  strategy?: string;
  threats?: Player[];
  differentials?: Player[];
  rival_chips?: { manager: string; chips: string[] }[];
}

export interface Elite {
  managers?: number;
  gameweek?: number;
  seasons?: string[];
  median_rank?: number;
  most_owned?: Player[];
  missing?: Player[];
  bought?: Player[];
  sold?: Player[];
}

export interface PlayerRef {
  id?: number;
  name: string;
  team?: string;
  in_your_team?: boolean;
}

export interface Creator {
  name: string;
  handle?: string;
  channel_url?: string;
  video?: { title?: string; url?: string; published?: string } | null;
  read?: boolean;
  summary?: string;
  considering_in?: PlayerRef[];
  considering_out?: PlayerRef[];
  captain_options?: PlayerRef[];
  decided_in?: PlayerRef[];
  decided_out?: PlayerRef[];
  captain?: PlayerRef | null;
  chip?: string | null;
  actual?: {
    gameweek?: number;
    transfers_in?: PlayerRef[];
    transfers_out?: PlayerRef[];
    captain?: PlayerRef | null;
    chip?: string | null;
    hit?: number;
  } | null;
}

export interface Consensus {
  kind: "buy" | "sell" | "captain" | "transfer" | string;
  player?: PlayerRef;
  count?: number;
  of?: number;
  who?: string[];
  text: string;
  buying?: string[];
  selling?: string[];
  options?: { player?: PlayerRef; who?: string[] }[];
}

export interface Creators {
  gameweek?: number;
  creators: Creator[];
  agree?: Consensus[];
  split?: Consensus[];
  with_plan?: number;
  with_video?: number;
  read_by_llm?: boolean;
  updated?: string;
}

export interface Advice {
  team?: {
    id?: number;
    name?: string;
    manager?: string;
    total_points?: number;
    value?: number;
    bank?: number;
    free_transfers?: number;
  };
  gameweek?: { id?: number; deadline?: string; hours_to_deadline?: number };
  horizon?: number[];
  initial?: boolean;
  transfers?: Transfers | null;
  captain?: {
    captain?: Player;
    vice?: Player;
    multiplier?: number;
    options?: Player[];
    note?: string | null;
  } | null;
  lineup?: Lineup | null;
  chips?: Chip[];
  chip_now?: string | null;
  chip_now_code?: string | null;
  timing?: string[];
  league?: League | null;
  elite?: Elite | null;
  creators?: Creators | null;
  sources?: Record<string, string>;
  notes?: string[];
}
