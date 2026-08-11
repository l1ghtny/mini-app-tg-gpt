from __future__ import annotations

import json
from pathlib import Path

import yaml


_MANIFEST = (
    Path(__file__).parents[1]
    / "k8s"
    / "argo-rollouts"
    / "lightny-work-runs-observability.yaml"
)

_RESET_SAFE_PANELS = {
    "Result validation pass rate",
    "Artifact contract pass rate",
    "Average sources per result",
    "Generated artifacts in range",
    "Provider tool calls",
}


def test_work_quality_dashboard_queries_include_first_counter_sample() -> None:
    documents = yaml.safe_load_all(_MANIFEST.read_text())
    config_map = next(
        document
        for document in documents
        if document.get("kind") == "ConfigMap"
        and document["metadata"]["name"]
        == "lightny-work-runs-grafana-dashboard"
    )
    dashboard = json.loads(config_map["data"]["lightny-work-runs.json"])
    panels = {panel["title"]: panel for panel in dashboard["panels"]}

    assert _RESET_SAFE_PANELS <= panels.keys()
    for title in _RESET_SAFE_PANELS:
        expression = panels[title]["targets"][0]["expr"]
        assert "min_over_time(" in expression
        assert " offset " in expression
