import csv
import json
import statistics
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.ai import provider
from app.ai.rules import (
    RULES_VERSION,
    check_business_rules,
    prepare_reviews,
    rule_issues,
    text_leaves,
)
from app.config import Settings
from app.errors import AppError
from app.services.budget import maximum_call_cost
from app.services.images import prepare_image, write_image

SUMMARY_FIELDS = ["sample_id", "category", "schema_ok", "rule_errors", "warnings", "forbidden_found",
                  "unknown_unflagged", "reviews", "latency_ms", "input_tokens", "output_tokens", "cost_usd", "error"]


def load_samples(dataset: Path) -> list[dict[str, Any]]:
    samples = []
    for folder in sorted(path for path in (dataset / "samples").iterdir() if path.is_dir()):
        spec = json.loads((folder / "sample.json").read_text(encoding="utf-8"))
        samples.append({"id": folder.name, "folder": folder, "notes": spec.get("notes", ""),
                        "images": spec["images"], "category": spec.get("category", ""),
                        "forbidden_terms": spec.get("forbidden_terms", []), "unknown_fields": spec.get("unknown_fields", [])})
    return samples


def result_cost(settings: Settings, result: provider.ProviderResult) -> Decimal | None:
    if result.input_tokens is None or result.output_tokens is None:
        return None
    output = result.output_tokens + (result.thinking_tokens or 0)
    return (result.input_tokens * settings.ai_input_price_per_million
            + output * settings.ai_output_price_per_million) / Decimal("1000000")


def assess(sample: dict[str, Any], content) -> dict[str, Any]:
    data = content.model_dump()
    text = " ".join(value for _, value in text_leaves(data)).casefold()
    notes = sample["notes"].casefold()
    forbidden = [term for term in sample["forbidden_terms"] if term.casefold() in text and term.casefold() not in notes]
    reviewed = {item["path"].split(".")[0] for item in data["review_items"]}
    unflagged = [field for field in sample["unknown_fields"]
                 if field not in reviewed and any(data.get(field, {}).get(language) for language in ("uz", "ru"))]
    errors, warnings = rule_issues(content)
    return {"schema_ok": True, "rule_errors": len(errors), "warnings": len(warnings), "forbidden_found": "; ".join(forbidden),
            "unknown_unflagged": "; ".join(unflagged), "reviews": len(data["review_items"])}


async def run_sample(settings: Settings, sample: dict[str, Any], upload_root: Path) -> tuple[dict, dict | None]:
    row: dict[str, Any] = {"sample_id": sample["id"], "category": sample["category"], "schema_ok": False, "error": ""}
    keys = []
    for name in sample["images"]:
        keys.append(write_image(upload_root, prepare_image((sample["folder"] / name).read_bytes())))
    snapshot = {"seller_notes": sample["notes"], "content": None, "field_path": None,
                "images": [{"storage_key": key} for key in keys]}
    started = time.perf_counter()
    try:
        result = await provider.generate(settings, snapshot)
        row["latency_ms"] = round((time.perf_counter() - started) * 1000)
        row.update(input_tokens=result.input_tokens, output_tokens=result.output_tokens, cost_usd=result_cost(settings, result))
        content = provider.parse_result(result, snapshot)
        check_business_rules(content)
        content = prepare_reviews(content)
    except (provider.ProviderFailure, AppError) as error:
        row["error"] = getattr(error, "code", type(error).__name__)
        return row, None
    except ValueError as error:
        row["error"] = f"invalid_output: {str(error)[:160]}"
        return row, None
    row.update(assess(sample, content))
    return row, content.model_dump()


def ratings(dataset: Path) -> dict[str, dict[str, str]]:
    path = dataset / "ratings.csv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as source:
        return {row["sample_id"]: row for row in csv.DictReader(source)}


def percentile(values: list[int], share: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(share * (len(ordered) - 1)))]


def summarize(settings: Settings, rows: list[dict], rated: dict[str, dict], live: bool) -> dict[str, Any]:
    latencies = [row["latency_ms"] for row in rows if row.get("latency_ms") is not None]
    known_costs = [row["cost_usd"] for row in rows if row.get("cost_usd") is not None]
    usable = sum(1 for row in rows if rated.get(row["sample_id"], {}).get("usable", "").lower() in {"yes", "ha", "1"}
                 and float(rated[row["sample_id"]].get("edit_seconds") or 9999) <= 180)
    critical = sum(1 for row in rows if rated.get(row["sample_id"], {}).get("critical_fact_error", "").lower() in {"yes", "ha", "1"})
    automated = sum(1 for row in rows if row.get("schema_ok") and not row.get("forbidden_found") and not row.get("unknown_unflagged"))
    complete = live and len(rows) >= 20 and len(rated) >= len(rows)
    return {"mode": "live" if live else "mock", "model": settings.ai_model, "prompt_version": provider.PROMPT_VERSION,
            "rules_version": RULES_VERSION, "price_version": settings.ai_price_version, "samples": len(rows),
            "automated_pass": automated, "human_rated": len(rated), "usable_within_3_minutes": usable,
            "critical_fact_errors": critical, "latency_median_ms": statistics.median(latencies) if latencies else None,
            "latency_p95_ms": percentile(latencies, 0.95), "cost_usd_reported": str(sum(known_costs, Decimal("0"))),
            "calls_with_unknown_cost": len(rows) - len(known_costs),
            "accepted": bool(complete and usable >= 17 * len(rows) / 20 and critical == 0 and automated == len(rows)),
            "acceptance_note": "Mock runs never prove AI quality." if not live else "Requires 20+ samples with seller ratings."}


def write_outputs(output: Path, rows: list[dict], contents: dict[str, Any], summary: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with (output / "results.csv").open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    (output / "cards.json").write_text(json.dumps(contents, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    template = output / "ratings_template.csv"
    with template.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.writer(target)
        writer.writerow(["sample_id", "usable", "edit_seconds", "critical_fact_error", "notes"])
        writer.writerows([[row["sample_id"], "", "", "", ""] for row in rows])


async def evaluate(settings: Settings, dataset: Path, output: Path, live: bool, max_cost_usd: float) -> dict[str, Any]:
    if live and settings.ai_provider != "gemini":
        raise SystemExit("--live requires AI_PROVIDER=gemini with a verified budget")
    run_settings = settings if live else settings.model_copy(update={"ai_provider": "mock", "app_env": "test"})
    rows, contents, spent = [], {}, Decimal("0")
    with tempfile.TemporaryDirectory(prefix="karto-eval-") as directory:
        run_settings = run_settings.model_copy(update={"upload_dir": Path(directory)})
        for sample in load_samples(dataset):
            if live and spent + maximum_call_cost(run_settings) > Decimal(str(max_cost_usd)):
                rows.append({"sample_id": sample["id"], "category": sample["category"], "error": "eval_budget_reached"})
                continue
            row, content = await run_sample(run_settings, sample, Path(directory))
            spent += row.get("cost_usd") or (maximum_call_cost(run_settings) if live else Decimal("0"))
            rows.append(row)
            if content:
                contents[sample["id"]] = content
    summary = summarize(run_settings, rows, ratings(dataset), live)
    write_outputs(output, rows, contents, summary)
    return summary
