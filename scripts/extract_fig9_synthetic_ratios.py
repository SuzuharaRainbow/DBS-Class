#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

import pdfplumber


RATIO_RE = re.compile(r"^\d+(?:\.\d+)?x$")


@dataclass(frozen=True)
class RatioToken:
    value: float
    x: float
    y: float


def _extract_ratio_tokens(page) -> list[RatioToken]:
    chars = [
        c
        for c in page.chars
        if (c.get("upright") is False) and (c.get("text") in "0123456789.x")
    ]

    # Group rotated vertical label characters by x0 (same column).
    groups: dict[float, list[dict]] = {}
    for c in chars:
        groups.setdefault(round(float(c["x0"]), 1), []).append(c)

    tokens: list[RatioToken] = []
    for _, group_chars in groups.items():
        group_chars.sort(key=lambda c: float(c["top"]))

        segment: list[dict] = []
        prev = None
        for c in group_chars:
            if prev is None:
                segment = [c]
                prev = c
                continue

            if abs(float(c["x0"]) - float(prev["x0"])) < 0.5 and 0 <= float(c["top"]) - float(
                prev["top"]
            ) <= 6:
                segment.append(c)
            else:
                tokens.extend(_segment_to_tokens(segment))
                segment = [c]
            prev = c

        tokens.extend(_segment_to_tokens(segment))

    return tokens


def _segment_to_tokens(segment: list[dict]) -> list[RatioToken]:
    if not segment:
        return []
    raw = "".join(str(ch["text"]) for ch in segment)
    if "x" not in raw:
        return []
    # Text is vertical, so reverse characters to get the natural string (e.g., "x13.0" -> "0.31x").
    s = raw[::-1]
    if not RATIO_RE.match(s):
        return []

    value = float(s[:-1])
    x = statistics.mean((float(ch["x0"]) + float(ch["x1"])) / 2 for ch in segment)
    y = statistics.mean((float(ch["top"]) + float(ch["bottom"])) / 2 for ch in segment)
    return [RatioToken(value=value, x=x, y=y)]


def _dataset_bounds(page, dataset_labels: list[str]) -> dict[str, tuple[float, float]]:
    words = page.extract_words(keep_blank_chars=False, use_text_flow=True)
    labels = []
    for w in words:
        if w["text"] in dataset_labels:
            center = (float(w["x0"]) + float(w["x1"])) / 2
            labels.append((w["text"], center))
    labels.sort(key=lambda t: t[1])

    names = [n for n, _ in labels]
    centers = [c for _, c in labels]
    bounds: dict[str, tuple[float, float]] = {}
    for i, (name, c) in enumerate(labels):
        left = (centers[i - 1] + c) / 2 if i > 0 else 0.0
        right = (c + centers[i + 1]) / 2 if i + 1 < len(centers) else float(page.width)
        bounds[name] = (left, right)

    missing = [d for d in dataset_labels if d not in bounds]
    if missing:
        raise RuntimeError(f"Missing dataset labels on page: {missing}")
    return bounds


def extract_fig9_synthetic_ratios(
    pdf_path: Path,
    output_csv: Path,
    page_number: int = 16,
) -> None:
    datasets_all = [
        "amzn",
        "wiki",
        "osmc",
        "face",
        "syn_g10_l1",
        "syn_g10_l2",
        "syn_g10_l4",
        "syn_g12_l1",
        "syn_g12_l4",
    ]
    datasets_synthetic = [
        "syn_g10_l1",
        "syn_g10_l2",
        "syn_g10_l4",
        "syn_g12_l1",
        "syn_g12_l4",
    ]

    # Fig.9 page index is 16 in the PDF file (1-based page number).
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[page_number - 1]

        bounds = _dataset_bounds(page, datasets_all)
        all_tokens = _extract_ratio_tokens(page)

        rows: list[dict[str, str]] = []
        for ds in datasets_synthetic:
            left, right = bounds[ds]
            in_col = [t for t in all_tokens if left <= t.x <= right and 80 < t.y < 230 and t.value <= 5]
            top = sorted((t for t in in_col if t.y < 160), key=lambda t: t.x)
            bot = sorted((t for t in in_col if t.y >= 160), key=lambda t: t.x)

            for rp, group in [(1.05, top), (5.0, bot)]:
                if len(group) != 4:
                    raise RuntimeError(
                        f"Unexpected number of ratio labels for {ds} Rp={rp}: "
                        f"got {len(group)}, values={[t.value for t in group]}"
                    )

                # The PDF only exposes 4 numeric labels per subplot for these datasets:
                # PGM (1.00x) + one PGM-based compressed variant + two LeCo-Zonemap variants.
                # We map by left-to-right order.
                mapping = {
                    "PGM": group[0].value,
                    "CprLeCo_PGM_D": group[1].value,
                    "LeCo-ZoneMap": group[2].value,
                    "LeCo-ZoneMap-D": group[3].value,
                }

                for index_name, ratio in mapping.items():
                    rows.append(
                        {
                            "Dataset": ds,
                            "Rp (pages)": f"{rp:g}",
                            "Index": index_name,
                            "Paper Ratio": f"{ratio:.2f}",
                        }
                    )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["Dataset", "Rp (pages)", "Index", "Paper Ratio"])
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Fig.9 synthetic ratio labels from the paper PDF.")
    parser.add_argument(
        "--pdf",
        type=Path,
        default=Path("learned-index-disk-sigmod24.pdf"),
        help="Path to the paper PDF (default: learned-index-disk-sigmod24.pdf).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("results/synthetic_fig9_paper_ratios.csv"),
        help="Output CSV path.",
    )
    parser.add_argument(
        "--page",
        type=int,
        default=16,
        help="1-based page number containing Fig.9 (default: 16).",
    )
    args = parser.parse_args()

    extract_fig9_synthetic_ratios(pdf_path=args.pdf, output_csv=args.out, page_number=args.page)


if __name__ == "__main__":
    main()

