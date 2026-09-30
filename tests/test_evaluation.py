import json

from PIL import Image

from app.config import Settings
from app.evaluation import evaluate


async def test_mock_evaluation_writes_reports_but_never_accepts_quality(tmp_path):
    dataset = tmp_path / "dataset"
    for index, forbidden in enumerate((["Samsung"], [])):
        folder = dataset / "samples" / f"sample-{index + 1}"
        folder.mkdir(parents=True)
        Image.new("RGB", (60, 80), "white").save(folder / "front.png")
        (folder / "sample.json").write_text(json.dumps({
            "category": "Idishlar", "notes": "Oq chashka", "images": ["front.png"],
            "forbidden_terms": forbidden, "unknown_fields": ["material"],
        }), encoding="utf-8")
    output = tmp_path / "report"
    summary = await evaluate(Settings(_env_file=None, app_env="test"), dataset, output, live=False, max_cost_usd=0)
    assert summary["mode"] == "mock" and summary["samples"] == 2 and summary["automated_pass"] == 2
    assert summary["accepted"] is False
    assert {path.name for path in output.iterdir()} == {"results.csv", "cards.json", "summary.json", "ratings_template.csv"}
