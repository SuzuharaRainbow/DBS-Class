#!/usr/bin/env python3
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber


def fmt_int(n: int) -> str:
    return f"{n:,}"


def fmt_list_int(ns: list[int]) -> str:
    return " / ".join(fmt_int(n) for n in ns)


def fmt_float(x: float, nd: int = 2) -> str:
    return f"{x:.{nd}f}"


def fmt_mib(x: float) -> str:
    # Keep 2 decimals like paper table.
    return f"{x:.2f} MiB"


def parse_paper_table3(pdf_path: Path) -> dict[str, dict[str, list[int] | list[float]]]:
    # Table 3 is on page 12 (1-based).
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[11].extract_text() or ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    wo = [ln for ln in lines if ln.startswith("#Models(w/o)")]
    wi = [ln for ln in lines if ln.startswith("#Models(w/)")]
    sv = [ln for ln in lines if ln.startswith("%ofSaving")]
    if len(wo) < 2 or len(wi) < 2 or len(sv) < 2:
        raise RuntimeError("Failed to parse Table 3 lines from PDF.")

    def nums_int(line: str) -> list[int]:
        return [int(x.replace(",", "")) for x in re.findall(r"([0-9][0-9,]*)", line)]

    def nums_pct(line: str) -> list[float]:
        return [float(x) for x in re.findall(r"([0-9]+\.?[0-9]*)%", line)]

    # Each line has 8 numbers: PGM(4 rp) + RS(4 rp)
    paper = {
        "fb": {"wo": nums_int(wo[0]), "wi": nums_int(wi[0]), "sv": nums_pct(sv[0])},
        "amzn": {"wo": nums_int(wo[1]), "wi": nums_int(wi[1]), "sv": nums_pct(sv[1])},
    }
    return paper


def parse_paper_table4(pdf_path: Path) -> dict[str, dict[str, float]]:
    # Table 4 is on page 14 (1-based).
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[13].extract_text() or ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    wanted = {}
    for ln in lines:
        if re.match(r"^(Cpr_PGM|CprLeCo_PGM_D|ZoneMap|LeCo-basedZoneMap)\b", ln):
            name = ln.split()[0]
            vals = re.findall(r"([0-9]+\.[0-9]+)MiB", ln)
            if len(vals) != 4:
                continue
            wanted[name] = [float(v) for v in vals]
    if len(wanted) != 4:
        raise RuntimeError("Failed to parse Table 4 rows from PDF.")

    # Columns order in paper: fb amzn wiki osmc
    cols = ["fb", "amzn", "wiki", "osmc"]
    out: dict[str, dict[str, float]] = {c: {} for c in cols}
    for idx_name, vals in wanted.items():
        for c, v in zip(cols, vals, strict=True):
            out[c][idx_name] = v
    return out


@dataclass(frozen=True)
class Table3Ours:
    pgm_wo: list[int]
    pgm_wi: list[int]
    pgm_sv: list[float]
    rs_wo: list[int]
    rs_wi: list[int]
    rs_sv: list[float]


def parse_ours_table3(csv_path: Path) -> Table3Ours:
    # Format: Expected I/O pages,Index,Base Models,Improved Models,Reduction %
    pgm_base: list[int] = []
    pgm_impr: list[int] = []
    pgm_sav: list[float] = []
    rs_base: list[int] = []
    rs_impr: list[int] = []
    rs_sav: list[float] = []

    with csv_path.open() as f:
        r = csv.DictReader(f)
        for row in r:
            idx = row["Index"]
            base = int(row["Base Models"])
            impr = int(row["Improved Models"])
            sav = float(row["Reduction %"])
            if idx == "PGM":
                pgm_base.append(base)
                pgm_impr.append(impr)
                pgm_sav.append(sav)
            elif idx == "RadixSpline":
                rs_base.append(base)
                rs_impr.append(impr)
                rs_sav.append(sav)

    return Table3Ours(pgm_base, pgm_impr, pgm_sav, rs_base, rs_impr, rs_sav)


def parse_ours_table4(csv_path: Path) -> dict[str, dict[str, float]]:
    # Dataset,Index,Models,Space (MiB)
    out: dict[str, dict[str, float]] = {}
    with csv_path.open() as f:
        r = csv.DictReader(f)
        for row in r:
            ds = row["Dataset"]
            idx = row["Index"]
            mib = float(row["Space (MiB)"])
            out.setdefault(ds, {})[idx] = mib
    return out


def max_abs_rel(paper: list[int] | list[float], ours: list[int] | list[float]) -> tuple[float, float]:
    if len(paper) != len(ours):
        return (float("nan"), float("nan"))
    abs_max = 0.0
    rel_max = 0.0
    for p, o in zip(paper, ours, strict=True):
        d = abs(float(o) - float(p))
        abs_max = max(abs_max, d)
        if float(p) != 0:
            rel_max = max(rel_max, d / abs(float(p)))
    return abs_max, rel_max


def main() -> int:
    pdf_path = Path("learned-index-disk-sigmod24.pdf")
    paper_t3 = parse_paper_table3(pdf_path)
    paper_t4 = parse_paper_table4(pdf_path)

    ours_t3_fb = parse_ours_table3(Path("results/fb_table3_reproduction.csv"))
    ours_t3_amzn = parse_ours_table3(Path("results/books_table3_reproduction.csv"))
    ours_t4 = parse_ours_table4(Path("results/table4_reproduction_all.csv"))

    # Map our dataset names to paper columns.
    ds_map = {
        "fb_200M_uint64": "fb",
        "books_200M_uint64": "amzn",
        "wiki_ts_200M_uint64": "wiki",
        "osm_cellids_200M_uint64": "osmc",
    }

    # Our Table4 index labels
    ours_idx_map = {
        "Cpr_PGM": "Cpr_PGM",
        "CprLeCo_PGM_D": "CprLeCo_PGM_D",
        "ZoneMap": "ZoneMap",
        "LeCo-basedZoneMap": "LeCo-basedZoneMap",
    }

    rows: list[list[str]] = []
    rows.append(["Dataset", "Metric", "Paper", "Ours", "Δ (max)"])

    def add_row(dataset: str, metric: str, paper_val: str, ours_val: str, delta: str) -> None:
        rows.append([dataset, metric, paper_val, ours_val, delta])

    # Table 3: fb and amzn
    for paper_ds, ours_t3, label in [
        ("fb", ours_t3_fb, "fb_200M_uint64"),
        ("amzn", ours_t3_amzn, "books_200M_uint64"),
    ]:
        wo = paper_t3[paper_ds]["wo"]
        wi = paper_t3[paper_ds]["wi"]
        sv = paper_t3[paper_ds]["sv"]

        # split into PGM(4) + RS(4)
        paper_pgm_wo = wo[0:4]
        paper_pgm_wi = wi[0:4]
        paper_pgm_sv = sv[0:4]
        paper_rs_wo = wo[4:8]
        paper_rs_wi = wi[4:8]
        paper_rs_sv = sv[4:8]

        # ours savings already in %
        ours_pgm_sv = [round(x, 1) for x in ours_t3.pgm_sv]
        ours_rs_sv = [round(x, 1) for x in ours_t3.rs_sv]

        a, r = max_abs_rel(paper_pgm_wo, ours_t3.pgm_wo)
        add_row(label, "Table 3 / PGM #Models(w/o) @Rp=1.01/1.5/2/3", fmt_list_int(paper_pgm_wo), fmt_list_int(ours_t3.pgm_wo), f"abs≤{int(a):,}, rel≤{r*100:.3f}%")

        a, r = max_abs_rel(paper_pgm_wi, ours_t3.pgm_wi)
        add_row(label, "Table 3 / PGM #Models(w/ ) @Rp=1.01/1.5/2/3", fmt_list_int(paper_pgm_wi), fmt_list_int(ours_t3.pgm_wi), f"abs≤{int(a):,}, rel≤{r*100:.3f}%")

        a, r = max_abs_rel(paper_pgm_sv, ours_pgm_sv)
        add_row(label, "Table 3 / PGM %Saving @Rp=1.01/1.5/2/3", " / ".join(f"{x:.1f}%" for x in paper_pgm_sv), " / ".join(f"{x:.1f}%" for x in ours_pgm_sv), f"abs≤{a:.2f}%, rel≤{r*100:.3f}%")

        a, r = max_abs_rel(paper_rs_wo, ours_t3.rs_wo)
        add_row(label, "Table 3 / RadixSpline #Models(w/o) @Rp=1.01/1.5/2/3", fmt_list_int(paper_rs_wo), fmt_list_int(ours_t3.rs_wo), f"abs≤{int(a):,}, rel≤{r*100:.3f}%")

        a, r = max_abs_rel(paper_rs_wi, ours_t3.rs_wi)
        add_row(label, "Table 3 / RadixSpline #Models(w/ ) @Rp=1.01/1.5/2/3", fmt_list_int(paper_rs_wi), fmt_list_int(ours_t3.rs_wi), f"abs≤{int(a):,}, rel≤{r*100:.3f}%")

        a, r = max_abs_rel(paper_rs_sv, ours_rs_sv)
        add_row(label, "Table 3 / RadixSpline %Saving @Rp=1.01/1.5/2/3", " / ".join(f"{x:.1f}%" for x in paper_rs_sv), " / ".join(f"{x:.1f}%" for x in ours_rs_sv), f"abs≤{a:.2f}%, rel≤{r*100:.3f}%")

    # Table 4: 4 datasets x 4 indexes
    for ours_ds, paper_col in ds_map.items():
        for idx in ["Cpr_PGM", "CprLeCo_PGM_D", "ZoneMap", "LeCo-basedZoneMap"]:
            paper_v = paper_t4[paper_col][idx]
            ours_v = ours_t4[ours_ds][ours_idx_map[idx]]
            d = abs(ours_v - paper_v)
            rel = d / paper_v if paper_v != 0 else 0.0
            add_row(
                ours_ds,
                f"Table 4 / {idx} Memory",
                fmt_mib(paper_v),
                fmt_mib(ours_v),
                f"{d:.2f} MiB ({rel*100:.2f}%)",
            )

    # Synthetic: one explicit textual comparison from paper (Fig.9 discussion).
    syn_path = Path("results/synthetic_fig9_reproduction.csv")
    if syn_path.exists():
        syn = list(csv.DictReader(syn_path.open()))
        def get_ratio(ds: str, rp: str, idx: str) -> float:
            for r in syn:
                if r["Dataset"] == ds and r["Rp (pages)"] == rp and r["Index"] == idx:
                    return float(r["Ratio to PGM"])
            raise KeyError((ds, rp, idx))
        ours_ratio = (
            get_ratio("syn_g12_l4", "5", "CprLeCo_PGM_D")
            / get_ratio("syn_g12_l4", "5", "LeCo-ZoneMap-D")
        )
        add_row(
            "syn_g12_l4",
            "Fig.9 text: (Rp=5) CprLeCo_PGM_D / LeCo-ZoneMap-D",
            "1.68× (paper text)",
            f"{ours_ratio:.2f}×",
            "note: depends on LeCo-ZoneMap-D tuning",
        )

    # Emit as markdown table
    md = []
    md.append("| " + " | ".join(rows[0]) + " |")
    md.append("|" + "|".join(["---"] * len(rows[0])) + "|")
    for r in rows[1:]:
        md.append("| " + " | ".join(r) + " |")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

