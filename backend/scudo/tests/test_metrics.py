"""CloudWatch EMF metric emission: silent outside Lambda, EMF line inside."""

from __future__ import annotations

import json


def test_emit_noop_outside_lambda(monkeypatch, capsys):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    from scudo import metrics

    metrics.emit("BandPass", 1)
    assert capsys.readouterr().out == ""


def test_emit_prints_emf_in_lambda(monkeypatch, capsys):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "scudo-fn")
    from scudo import metrics

    metrics.emit("BandPass", 1, dims={"vendor": "LSEG"})
    line = json.loads(capsys.readouterr().out.strip())

    assert line["BandPass"] == 1
    assert line["vendor"] == "LSEG"
    cw = line["_aws"]["CloudWatchMetrics"][0]
    assert cw["Namespace"] == "SCUDO"
    assert cw["Metrics"][0] == {"Name": "BandPass", "Unit": "Count"}
    assert cw["Dimensions"] == [["vendor"]]


def test_emit_no_dims_uses_empty_dimension_set(monkeypatch, capsys):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "scudo-fn")
    from scudo import metrics

    metrics.emit("EtlPassed")
    line = json.loads(capsys.readouterr().out.strip())

    assert line["EtlPassed"] == 1
    assert line["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [[]]


def test_emit_default_value_is_one(monkeypatch, capsys):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "scudo-fn")
    from scudo import metrics

    metrics.emit("PollerPages", 3, dims={"vendor": "lseg"})
    line = json.loads(capsys.readouterr().out.strip())
    assert line["PollerPages"] == 3
