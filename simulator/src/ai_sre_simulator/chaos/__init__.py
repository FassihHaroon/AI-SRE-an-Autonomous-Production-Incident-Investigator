"""chaos: the simulation control plane (fault injection, scenarios, reset).

It is deliberately invisible to the monitoring stack: its logs are not shipped to Loki and its
metrics are not scraped. An investigator sees the effects of a fault, never the fault itself.
"""
