"""The simulated shop: api-gateway, orders-service, fulfilment-worker, payments-provider."""

FULFILMENT_QUEUE = "queue:fulfilment"
FULFILMENT_DLQ = "queue:fulfilment:dead"
