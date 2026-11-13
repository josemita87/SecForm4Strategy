# Error handling

Every service handles failures by one contract. Adapters retry briefly and then classify what is left. Services act on that class the same way, whatever the service.

## Adapters: retry, then classify

An adapter retries transient failures itself, with exponential backoff and jitter, for a bounded number of attempts. It is the only layer that knows its SDK's errors and rate limits. Calls whose SDK already retries are not wrapped again: botocore retries AWS calls, and alpaca-py retries 429 and 504.

SEC's rate-limit 403 is not retried in the adapter. SEC locks the client out for about 10 minutes, and requests during the lockout extend it, so the adapter raises `RetryableError` at once.

When the retries run out, or the failure is not transient, the adapter raises one of two exceptions from `secform4strategy_clients.ports`:

| Exception | Meaning | Examples |
| --- | --- | --- |
| `RetryableError` | The operation failed for now and is safe to repeat later | Timeouts, connection errors, HTTP 429 and 5xx, SEC's rate-limit 403, Iceberg commit conflicts |
| `PermanentError` | The operation fails however often it is repeated | HTTP 404, a request the API rejects as invalid |

The adapter raises them `from` the SDK exception, so the traceback keeps the cause and services never import SDK exception types.

An adapter raises `RetryableError` only for operations that are safe to repeat. A GET or an upsert by key qualifies. An order qualifies only when it carries a `client_order_id`, so the broker rejects a duplicate.

An expected empty answer is a return value, not an error. A filing without an ownership document returns `None`.

## Services: one rule per unit of work

A unit of work is a message, a batch task, or an item inside a task. Each has a done marker:

| Unit of work | Done marker |
| --- | --- |
| Message | The ack |
| Batch task | Exit code 0 |
| Item inside a task | Its output is stored, so a rerun skips it |

The rule is the same for all three:

- **`RetryableError`:** the unit is not done. The service leaves the done marker unset, and the platform retries the unit later.
- **`PermanentError`:** the unit will never succeed. The service logs it with its identifier, then sets the done marker.
- **Any other exception** is a defect. The service does not catch it, and the process crashes.

A task keeps going after an item fails with `RetryableError`, and exits non-zero once all items have run. Step Functions then retries the task.

Services never write `except Exception`.

## Where retries happen

| Layer | Time scale | Mechanism |
| --- | --- | --- |
| Adapter | Seconds | Backoff inside the call |
| Platform | Minutes | SQS redelivers an unacked message after its visibility timeout; Step Functions `Retry` reruns a failed task |
| Dead-letter queue | Indefinite | SQS moves a message that keeps failing to the queue's DLQ; a CloudWatch alarm reports it |

A crash leaves its messages unacked, so a deterministic defect ends in the DLQ, and a crashed task fails its execution.

Kafka has no visibility timeout and no DLQ. Locally, the scraper waits `RETRY_PAUSE` seconds (default 60) after a batch stops on a `RetryableError`, and a message that fails on every delivery holds its partition until someone fixes the cause.

## Requirement: incremental tasks

A rerun task must skip the work already stored. Upserts by key and per-ticker start dates make every task meet this, so a retry redoes only what failed.
