#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def _fmt_ratio(v: float) -> str:
    return f"{v:.3f}"


def _fmt_diff(paper: float, ours: float) -> str:
    abs_diff = ours - paper
    rel = abs_diff / paper if paper != 0 else 0.0
    return f"{abs_diff:+.3f} ({rel:+.1%})"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a Markdown comparison table for Fig.9 synthetic datasets (paper vs reproduction)."
    )
    parser.add_argument(
        "--paper",
        type=Path,
        default=Path("results/synthetic_fig9_paper_ratios.csv"),
        help="CSV extracted from the paper (default: results/synthetic_fig9_paper_ratios.csv).",
    )
    parser.add_argument(
        "--ours",
        type=Path,
        default=Path("results/synthetic_fig9_reproduction.csv"),
        help="CSV summarized from our logs (default: results/synthetic_fig9_reproduction.csv).",
    )
    args = parser.parse_args()

    paper = pd.read_csv(args.paper)
    ours = pd.read_csv(args.ours)

    # Normalize types
    paper["Rp (pages)"] = paper["Rp (pages)"].astype(float)
    ours["Rp (pages)"] = ours["Rp (pages)"].astype(float)

    ours = ours[["Dataset", "Rp (pages)", "Index", "Ratio to PGM"]]
    merged = paper.merge(ours, on=["Dataset", "Rp (pages)", "Index"], how="left")

    order_dataset = ["syn_g10_l1", "syn_g10_l2", "syn_g10_l4", "syn_g12_l1", "syn_g12_l4"]
    order_rp = [1.05, 5.0]
    order_index = ["PGM", "CprLeCo_PGM_D", "LeCo-ZoneMap", "LeCo-ZoneMap-D"]

    merged["Dataset"] = pd.Categorical(merged["Dataset"], categories=order_dataset, ordered=True)
    merged["Rp (pages)"] = pd.Categorical(merged["Rp (pages)"], categories=order_rp, ordered=True)
    merged["Index"] = pd.Categorical(merged["Index"], categories=order_index, ordered=True)
    merged = merged.sort_values(["Dataset", "Rp (pages)", "Index"]).reset_index(drop=True)

    lines: list[str] = []
    lines.append("| Dataset | Rp | Index | 论文 Fig.9 比值 | 我们复现比值 | 差异(复现-论文) |")
    lines.append("|---|---:|---|---:|---:|---:|")
    for _, row in merged.iterrows():
        ds = str(row["Dataset"])
        rp = float(str(row["Rp (pages)"]))
        idx = str(row["Index"])
        paper_ratio = float(row["Paper Ratio"])
        ours_ratio = float(row["Ratio to PGM"])
        lines.append(
            "| "
            + " | ".join(
                [
                    ds,
                    f"{rp:g}",
                    idx,
                    _fmt_ratio(paper_ratio),
                    _fmt_ratio(ours_ratio),
                    _fmt_diff(paper_ratio, ours_ratio),
                ]
            )
            + " |"
        )

    print("\n".join(lines))


if __name__ == "__main__":
    main()

