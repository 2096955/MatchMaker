"""Safety tests for the stale-CDAO cleanup CLI.

The cleanup script does a destructive DETACH DELETE against a live FalkorDB.
These tests pin the safety contract WITHOUT a live database:

  * no flags  -> dry-run (NEVER deletes)
  * --apply   -> delete mode
  * --dry-run -> dry-run (explicit)
"""

from __future__ import annotations

from scudo.scripts.cleanup_stale_cdao import _parse_args, is_stale_cdao_iri


def _args(*flags):
    return _parse_args(["cleanup_stale_cdao", *flags])


def test_no_flags_defaults_to_dry_run():
    """A bare invocation must NOT delete — dry-run is the safe default."""
    args = _args()
    assert args.apply is False
    assert args.dry_run is True


def test_apply_flag_enables_delete():
    args = _args("--apply")
    assert args.apply is True
    assert args.dry_run is False


def test_explicit_dry_run_is_dry_run():
    args = _args("--dry-run")
    assert args.dry_run is True
    assert args.apply is False


def test_stale_iri_classifier():
    assert is_stale_cdao_iri("cdao:equities")
    assert is_stale_cdao_iri("urn:cdao:foo")
    assert is_stale_cdao_iri("jpmorgan:data:cdao:domain:Marketing")
    assert not is_stale_cdao_iri("jpmorgan:data:cdao:concept:equity-prices")
