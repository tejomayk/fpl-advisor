// Club colours per FPL short name: [primary, secondary, pattern]. No crests or logos.
export type Kit = readonly [string, string, "plain" | "sleeves" | "stripes"];

const KITS: Record<string, Kit> = {
  ARS: ["#DB0007", "#FFFFFF", "sleeves"],
  AVL: ["#7A1C45", "#95BFE5", "sleeves"],
  BOU: ["#DA291C", "#111111", "stripes"],
  BRE: ["#E30613", "#FFFFFF", "stripes"],
  BHA: ["#0057B8", "#FFFFFF", "stripes"],
  BUR: ["#6C1D45", "#99D6EA", "sleeves"],
  CHE: ["#034694", "#FFFFFF", "plain"],
  CRY: ["#1B458F", "#C4122E", "stripes"],
  EVE: ["#003399", "#FFFFFF", "plain"],
  FUL: ["#F2F2F0", "#111111", "plain"],
  IPS: ["#1F4FA0", "#FFFFFF", "plain"],
  LEE: ["#F2F2F0", "#1D428A", "plain"],
  LEI: ["#0053A0", "#FDBE11", "plain"],
  LIV: ["#C8102E", "#F6EB61", "plain"],
  MCI: ["#6CABDD", "#FFFFFF", "plain"],
  MUN: ["#DA291C", "#111111", "plain"],
  NEW: ["#111111", "#FFFFFF", "stripes"],
  NFO: ["#DD0000", "#FFFFFF", "plain"],
  SOU: ["#D71920", "#FFFFFF", "stripes"],
  SUN: ["#EB172B", "#FFFFFF", "stripes"],
  TOT: ["#F2F2F0", "#132257", "plain"],
  WHU: ["#7A263A", "#7FC4E8", "sleeves"],
  WOL: ["#FDB913", "#231F20", "plain"],
  HUL: ["#F5A12D", "#111111", "stripes"],
  COV: ["#6CC4EE", "#FFFFFF", "plain"],
  MID: ["#DE1B22", "#FFFFFF", "plain"],
  SHU: ["#EE2737", "#FFFFFF", "stripes"],
  NOR: ["#FFF200", "#00A650", "plain"],
  WBA: ["#122F67", "#FFFFFF", "stripes"],
  WAT: ["#FBEE23", "#ED2127", "plain"],
  SHW: ["#1E62B5", "#FFFFFF", "stripes"],
  STK: ["#E03A3E", "#FFFFFF", "stripes"],
  LUT: ["#F78F1E", "#002D62", "plain"],
};
const NEUTRAL: Kit = ["#7D8A82", "#FFFFFF", "plain"];

export const kitOf = (team?: string): Kit => KITS[String(team || "").toUpperCase()] || NEUTRAL;

/** Relative luminance, to pick legible text on a club colour. */
export function isLight(hex: string) {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.45;
}
