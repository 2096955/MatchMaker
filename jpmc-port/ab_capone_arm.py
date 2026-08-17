#!/usr/bin/env python3
"""DEPRECATED shim — Capone A/B arm lives under backend/scudo/scripts/.

Do not run this file. ``run_ab_compare.py`` invokes::

    python -P backend/scudo/scripts/ab_capone_arm.py

with ``PYTHONPATH=<repo>/backend``. The old layout (this file under jpmc-port/)
caused port-vs-port A/B because Python prepends the script directory to
``sys.path`` ahead of ``PYTHONPATH``.
"""

from __future__ import annotations


raise SystemExit(
    "FATAL: jpmc-port/ab_capone_arm.py is retired. "
    "Use: python -P backend/scudo/scripts/ab_capone_arm.py "
    "(via jpmc-port/run_ab_compare.py)."
)
