#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import glob
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MetricRow:
    dataset: str
    rp_target: str
    index: str
    models: int
    space_mib: float
    avg_page: float | None
    ratio_to_pgm: float | None


GET_MODEL_RE = re.compile(
    r"^GetModelNum of,\s*(?P<name>[^,]+),.*?#model:,(?P<models>\d+),\s*space/MiB:,(?P<mib>[0-9.]+)"
)
EVAL_RE = re.compile(
    r"^Evaluate index on disk:,\s*(?P<name>[^,]+),.*?avg_page:,(?P<avg_page>[0-9.]+).*?(?P<status>FIND SUCCESS|FIND WRONG)"
)


def dataset_from_path(path: Path) -> str:
    s = path.name
    # e.g., res_1219_paper_syn_g10_l1_8B_fetch0_syn_g10_l1.csv -> syn_g10_l1
    if "_fetch0_" in s:
        return s.split("_fetch0_", 1)[1].removesuffix(".csv")
    return s.removeprefix("res_").removesuffix(".csv")


def parse_file(path: Path) -> dict[str, list[dict]]:
    """
    Returns a dict index_name -> list of observations:
      {models, space_mib, avg_page, status}
    """
    last_get: dict[str, tuple[int, float]] = {}
    obs: dict[str, list[dict]] = {}
    with path.open("r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            m = GET_MODEL_RE.match(line)
            if m:
                name = m.group("name")
                last_get[name] = (int(m.group("models")), float(m.group("mib")))
                continue

            m = EVAL_RE.match(line)
            if not m:
                continue

            name = m.group("name")
            if name not in last_get:
                continue

            models, mib = last_get[name]
            avg_page = float(m.group("avg_page"))
            status = m.group("status")
            obs.setdefault(name, []).append(
                {"models": models, "space_mib": mib, "avg_page": avg_page, "status": status}
            )
    return obs


def choose_best(obs: list[dict], require_success: bool) -> dict | None:
    if require_success:
        obs = [o for o in obs if o["status"] == "FIND SUCCESS"]
    if not obs:
        return None
    return min(obs, key=lambda o: o["space_mib"])


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Summarize synthetic-dataset results aligned with paper Fig.9 (Rp=1.05 and 5 pages)."
    )
    ap.add_argument(
        "--glob",
        default="results/compression/res_*_paper_syn_*_8B_fetch0_syn_*.csv",
        help="Glob pattern for synthetic compression result files.",
    )
    ap.add_argument(
        "--out",
        default="results/synthetic_fig9_reproduction.csv",
        help="Output CSV path.",
    )
    args = ap.parse_args()

    files = [Path(p) for p in sorted(glob.glob(args.glob))]
    if not files:
        raise SystemExit(f"No files matched: {args.glob}")

    # Mapping from (rp_target, logical index label) to concrete index name in logs.
    targets: dict[tuple[str, str], str] = {
        ("1.05", "PGM"): "PGM Index Page_7",
        ("1.05", "Cpr_PGM"): "CompressedPGM_7",
        ("1.05", "CprLeCo_PGM_D"): "DI-V4_1.05",
        ("1.05", "ZoneMap"): "BinarySearch_256",
        ("1.05", "LeCo-ZoneMap"): "LecoZonemap_256",
        ("1.05", "LeCo-ZoneMap-D"): "LecoPage_256",
        ("5", "PGM"): "PGM Index Page_511",
        ("5", "Cpr_PGM"): "CompressedPGM_511",
        ("5", "CprLeCo_PGM_D"): "DI-V4_5.00",
        ("5", "ZoneMap"): "BinarySearch_1280",
        ("5", "LeCo-ZoneMap"): "LecoZonemap_1280",
        ("5", "LeCo-ZoneMap-D"): "LecoPage_1280",
    }

    rows: list[MetricRow] = []

    for path in files:
        dataset = dataset_from_path(path)
        parsed = parse_file(path)

        # Build per-Rp dict to compute ratio_to_pgm.
        per_rp: dict[str, dict[str, MetricRow]] = {"1.05": {}, "5": {}}

        for (rp, label), concrete in targets.items():
            if concrete not in parsed:
                continue

            best = choose_best(parsed[concrete], require_success=(label == "LeCo-ZoneMap-D"))
            if not best:
                continue

            per_rp[rp][label] = MetricRow(
                dataset=dataset,
                rp_target=rp,
                index=label,
                models=int(best["models"]),
                space_mib=float(best["space_mib"]),
                avg_page=float(best["avg_page"]),
                ratio_to_pgm=None,
            )

        for rp, items in per_rp.items():
            if not items:
                continue
            pgm = items.get("PGM")
            for label, r in items.items():
                ratio = (r.space_mib / pgm.space_mib) if (pgm and pgm.space_mib > 0) else None
                rows.append(
                    MetricRow(
                        dataset=r.dataset,
                        rp_target=r.rp_target,
                        index=r.index,
                        models=r.models,
                        space_mib=r.space_mib,
                        avg_page=r.avg_page,
                        ratio_to_pgm=ratio,
                    )
                )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Dataset", "Rp (pages)", "Index", "Models", "Space (MiB)", "Avg pages", "Ratio to PGM"])
        for r in sorted(rows, key=lambda x: (x.dataset, float(x.rp_target), x.index)):
            w.writerow(
                [
                    r.dataset,
                    r.rp_target,
                    r.index,
                    r.models,
                    f"{r.space_mib:.6f}".rstrip("0").rstrip("."),
                    f"{r.avg_page:.6f}".rstrip("0").rstrip(".") if r.avg_page is not None else "",
                    f"{r.ratio_to_pgm:.6f}".rstrip("0").rstrip(".") if r.ratio_to_pgm is not None else "",
                ]
            )

    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

