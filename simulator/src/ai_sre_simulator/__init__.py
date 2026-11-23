"""Simulated production environment for AI SRE.

A small distributed shop (API gateway, orders service, fulfilment worker, PostgreSQL, Redis)
with real telemetry (Prometheus metrics, JSON logs shipped to Loki, deployment records) and a
reversible fault-injection control plane. Everything here is **simulated**: no real customers,
payments or production systems are involved.
"""

__version__ = "0.2.0"

ENVIRONMENT = "simulated-prod"
"""Value of the ``environment`` field on every log line and metric series."""
