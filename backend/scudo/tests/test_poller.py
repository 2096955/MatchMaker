"""Config-driven vendor-API poller: streams pages to the raw bucket, fetches
keys from Secrets Manager, skips disabled vendors, and caps runaway pulls."""

from __future__ import annotations

import io
import json


class _FakeS3:
    def __init__(self):
        self.puts: list[dict] = []

    def put_object(self, **kw):
        self.puts.append(kw)


class _FakeSecrets:
    def get_secret_value(self, SecretId):
        return {"SecretString": json.dumps({"api_key": f"KEY-{SecretId}"})}


class _FakeHttp:
    def __init__(self, pages):
        self.pages = list(pages)
        self.seen_keys: list[str | None] = []

    def get(self, url, headers=None):
        self.seen_keys.append((headers or {}).get("x-api-key"))
        return self.pages.pop(0)


def test_poll_vendor_streams_each_page_to_raw_bucket(monkeypatch):
    monkeypatch.setenv("SCUDO_RAW_BUCKET", "raw-bkt")
    from scudo import poller_handler

    s3, secrets = _FakeS3(), _FakeSecrets()
    http = _FakeHttp([{"items": [1], "next": True}, {"items": [2], "next": False}])
    cfg = {
        "vendor": "lseg",
        "endpoint": "https://x",
        "secret_id": "scudo/poller/lseg",
        "enabled": True,
    }

    out = poller_handler.poll_vendor(cfg, s3=s3, secrets=secrets, http=http)

    assert out["pages"] == 2 and out["skipped"] is False
    assert all(p["Bucket"] == "raw-bkt" for p in s3.puts)
    assert [p["Key"] for p in s3.puts] == [
        "api/lseg/run/page-1.json",
        "api/lseg/run/page-2.json",
    ]
    # Key fetched from Secrets Manager, sent on every page, never in config.
    assert http.seen_keys == ["KEY-scudo/poller/lseg", "KEY-scudo/poller/lseg"]


def test_disabled_vendor_is_skipped(monkeypatch):
    monkeypatch.setenv("SCUDO_RAW_BUCKET", "raw-bkt")
    from scudo import poller_handler

    s3 = _FakeS3()
    out = poller_handler.poll_vendor(
        {"vendor": "off", "enabled": False},
        s3=s3,
        secrets=_FakeSecrets(),
        http=_FakeHttp([]),
    )
    assert out["skipped"] is True and out["pages"] == 0
    assert s3.puts == []


def test_max_pages_cap_truncates_and_does_not_loop(monkeypatch):
    monkeypatch.setenv("SCUDO_RAW_BUCKET", "raw-bkt")
    from scudo import poller_handler

    s3, secrets = _FakeS3(), _FakeSecrets()
    # Every page says next=True; the cap must stop the pull, not loop forever.
    http = _FakeHttp([{"items": [i], "next": True} for i in range(10)])
    cfg = {
        "vendor": "big",
        "endpoint": "https://x",
        "secret_id": "s",
        "enabled": True,
        "max_pages": 3,
    }

    out = poller_handler.poll_vendor(cfg, s3=s3, secrets=secrets, http=http)

    assert out["pages"] == 3 and out["truncated"] is True
    assert len(s3.puts) == 3


def test_load_config_reads_s3_uri(monkeypatch):
    from scudo import poller_handler

    class _CfgS3:
        def get_object(self, Bucket, Key):
            assert Bucket == "cfg-bkt" and Key == "poller/vendors.json"
            body = json.dumps({"vendors": [{"vendor": "a", "enabled": False}]}).encode(
                "utf-8"
            )
            return {"Body": io.BytesIO(body)}

    monkeypatch.setenv("SCUDO_POLLER_CONFIG", "s3://cfg-bkt/poller/vendors.json")
    vendors = poller_handler.load_config(s3=_CfgS3())
    assert vendors == [{"vendor": "a", "enabled": False}]
