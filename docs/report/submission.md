# SecForm4Strategy

**Cloud architecture for testing an insider-selling signal on AWS**

| | |
| --- | --- |
| Course | Cloud Solutions |
| Project | SecForm4Strategy |
| Date | 2026-11-23 |
| Repository | [https://github.com/josemita87/SecForm4Strategy](https://github.com/josemita87/SecForm4Strategy), branch `master` |

## Executive summary

**The question.** Corporate insiders must report their trades to the US Securities and Exchange Commission (SEC) on a Form 4. The project tests one hypothesis: insider sales come before the stock underperforms, and the market has not priced that in when the filing becomes public. If the hypothesis holds, the edge goes to the system that reacts first after publication.

**The system.** SecForm4Strategy reads every Form 4 from the SEC's EDGAR system, joins it to the stock prices after the filing became public, trains a gradient-boosted regression model to predict the 30-day return, and places short orders on a paper-trading account at the broker Alpaca. It runs on AWS in account `100118195151`, region `us-east-1`. Terraform defines every resource, and GitHub Actions deploys it through OIDC with an approval-gated, two-stage apply.

The architecture has two paths on one feature store:

- A **batch path** (EventBridge Scheduler, Step Functions, ECS Fargate on ARM64) loads years of filings and retrains the model each week.
- A **live path** (EventBridge Scheduler, SQS, Lambda container images, an EventBridge Pipe and a second state machine) scores each new filing within minutes of publication.

Both paths share the same container images and one Apache Iceberg lakehouse on S3 with the Glue catalog. A password-protected static website on CloudFront shows the live runs, the paper portfolio and the model, with no public API. Every component is serverless or switched off when idle. One Terraform switch takes the stack from about $0 per day when dormant to about $1.20 per day plus usage when switched on, under a $5 monthly budget.

**How to read this report.** Chapter 2 states the business problem and ends in ten requirements. Chapter 3 gives the data, the model and the engineering only as far as they shape the cloud design. Chapters 4 and 5 are the core: each architecture decision names the requirement that drives it, the alternative it was weighed against, and why it won.

**The outcome.** On one year of data, the model does not beat a constant zero prediction on mean absolute error (holdout MAE 0.1216 against 0.1193). Its most negative predictions still rank well: the 265 holdout rows predicted below -5% fell by 10.8% on average, before costs. The live path ran end to end on real filings. The deliverable is therefore cloud infrastructure that can test the insider-selling hypothesis rigorously, not a proven trading strategy.

## Contents

1. [Executive summary](#executive-summary)
2. [The business problem and requirements](#2-the-business-problem-and-requirements)
3. [Data, model and engineering in brief](#3-data-model-and-engineering-in-brief)
4. [Architecture overview](#4-architecture-overview)
5. [Architecture decisions by area](#5-architecture-decisions-by-area)
6. [Operations: the 5-year backfill](#6-operations-the-5-year-backfill)
7. [Results in brief](#7-results-in-brief)
8. [Design decisions and trade-offs](#8-design-decisions-and-trade-offs)
9. [Limitations and future work](#9-limitations-and-future-work)
10. [Conclusion](#10-conclusion)
11. [Appendix A: Resource inventory](#appendix-a-resource-inventory)
12. [Appendix B: Glossary](#appendix-b-glossary)

---

## 2. The business problem and requirements

### 2.1 The signal

A corporate insider is a director, an officer or an owner of more than 10% of a company. An insider who buys or sells the company's stock must file a Form 4 within two business days. The SEC publishes each Form 4 on EDGAR at the moment it accepts it, `accepted_at`.

Insiders know more about their company than the market does. The model trains on dispositions: sales, gifts, tax withholdings and other trades that reduce the insider's holding. A trader can act only after `accepted_at`, and other traders read the same filings. **Speed from disclosure is the edge**: a system that reacts faster captures more of the move.

### 2.2 The client

The target client is an investor who lets the system trade part of their money on this signal. The client needs to see what the system did with the money, and needs confidence that a broken model or a leaked key cannot trade.

### 2.3 Constraints

| Constraint | Value |
| --- | --- |
| SEC fair access | At most 10 requests per second per client, with one honest `User-Agent`. A breach returns HTTP 403 and a lockout of about 10 minutes. |
| Budget | $5 per month. The demo is on for the presentation and the backfill, off otherwise. |
| Broker | Alpaca paper trading; its keys can place and cancel orders |
| Repository | Public on GitHub, including its workflow artifacts |
| Data volume | About 250,000 filings per year; about 1.06 million in 5 years |

### 2.4 Requirements

Chapter 5 traces each AWS decision back to these ten requirements.

| ID | Requirement | Source |
| --- | --- | --- |
| R1 | React to a new filing within minutes of `accepted_at`, at near-zero cost while no filing arrives. | Business |
| R2 | Process years of history and run long jobs: a multi-day backfill and a weekly retrain. | Data, ML |
| R3 | Stay under the SEC limit of 10 requests per second, shared by every request with the project's `User-Agent`. | Constraint |
| R4 | Use no information that was not public at decision time, and compute features with one implementation for training and scoring. | ML |
| R5 | Support upserts, reprocessing and at-least-once delivery without duplicate rows or orders. | Data |
| R6 | Never let a bad or test model trade. Record what each model was trained on. | ML, business |
| R7 | Cost at most $5 per month, with a demo that switches on and off. | Constraint |
| R8 | Keep secrets and trading keys out of code, state and CI, in a public repository. | Security |
| R9 | Show a client what the system does with their money, without exposing the system. | Business |
| R10 | Operate and recover a multi-day backfill. | Operations |

---

## 3. Data, model and engineering in brief

This chapter keeps only what the cloud decisions depend on. The repository's `docs/` folder and the full report hold the details.

### 3.1 What flows through the system

Seven services do the work. Each is a separate Python 3.14 program with one job and its own container image. The same image runs as an ECS task on the batch path and as a Lambda function on the live path.

| Step | Service | Job | Writes |
| --- | --- | --- | --- |
| 1 | master-index | Lists Form 4 filings from EDGAR | One message per filing |
| 2 | scraper | Downloads and parses each filing | One message per transaction |
| 3 | kafka-to-store | Validates and upserts transactions | `BT4` (training) or `BI4` (live) |
| 4 | yahoo-connect | Downloads daily prices for the tickers | `P` |
| 5 | target | Builds one row per trading opportunity, with features and, for training, the label | `RT4` (labelled) or `BIR4` (to score) |
| 6 | training | Fits the model, compares it with baselines, saves it, and promotes it only through a gate | `models/` |
| 7 | inference | Scores rows and places short orders | Alpaca paper orders |

The five tables are Iceberg tables with deterministic keys. A transaction's key is a hash of its accession number, its table and its position, so a redelivered filing upserts the same row (R5).

| Data property | Value | Cloud consequence |
| --- | --- | --- |
| Live arrival | A few filings per minute in market hours, in bursts | Per-event compute that costs nothing between events (R1) |
| History | About 1.06 million filings in 5 years; the scraper runs at 6 to 7.8 filings per second | A load of days, beyond any 15-minute limit (R2) |
| Warehouse size | About 430 MB after the 5-year scrape | Object storage, no database server (R7) |

### 3.2 Constraints from the model

![A filing, from trade to label](../diagrams/filing-timeline.drawio.svg)

*Figure 1: One filing on the timeline (R4). The insider trades on the trade date. The SEC accepts the filing at `accepted_at`. Values use the last close before the acceptance day, the entry is the next market print, and the label ends at the first close at least 30 days later (`label_known_at`).*

| ML property | Value | Cloud consequence |
| --- | --- | --- |
| No look-ahead | Every row is timed by `accepted_at`. The label is the return from the next market print to the first close at least 30 days later. A label feeds later rows only after `label_known_at`. | Training and live scoring must compute features with the same code on the same tables: same images, one feature store (R4). |
| Model | A scikit-learn `Pipeline`: scaling, one-hot encoding and a `HistGradientBoostingRegressor` | One `model.joblib` file. No GPU, no hosted endpoint: the model loads in a Lambda or a task (R7). |
| Training | A walk-forward grid search: 32 parameter combinations on 4 purged time folds, so 128 fits, then one holdout score | About 2,200 CPU-seconds on one year. A 4 vCPU Fargate task fits it; Lambda's 15-minute limit does not (R2). |
| Retraining cadence | Weekly, on the last 7 days of filings | A scheduled batch run, idle the rest of the week (R2, R7) |
| Trading rule | Short 1 share for each row predicted below -5% (`PREDICTION_THRESHOLD`), with the row key as `client_order_id` | Idempotent orders under at-least-once delivery (R5) |

### 3.3 Engineering in brief

| Practice | What it is | Cloud consequence |
| --- | --- | --- |
| Separate projects | Each service is a `uv` project with a lockfile and a Dockerfile | One image and ECR repository per service, rebuilt only when its code changes |
| Ports and adapters | Services depend on Python `Protocol`s; `main.py` or `handler.py` builds the adapter | The local stack (Redpanda, SQLite, disk) and AWS (SQS, Glue, S3) run the same code; three settings select the backends |
| Error contract | An adapter retries transient errors, then raises `RetryableError` or `PermanentError` | SQS redelivery, Step Functions `Retry` and DLQs do the rest (Section 5.2) |
| Secrets as types | Credentials are `SecretStr`, unwrapped only at the SDK call | Secrets come from SSM at run time (Section 5.5) |
| Quality gate | ruff, pydoclint, bandit, config validation, unit tests without mocks, mypy | A required check before any merge and image build (Section 5.6) |

---

## 4. Architecture overview

### 4.1 The two paths

![AWS architecture](../diagrams/aws-architecture.drawio.svg)

*Figure 2: AWS architecture. The batch path (top) runs six ECS Fargate tasks under the `retrain` state machine (R2). The live path (middle) runs six Lambda functions on the same images (R4), chained by SQS queues, an EventBridge Pipe and the `live-score` state machine (R1, R5). Orange dashed elements exist only while the stack is switched on (R7).*

### 4.2 Requirements traceability

| Requirement | Source | AWS decision | Section |
| --- | --- | --- | --- |
| R1 Speed from disclosure | Business | Separate live path: Scheduler polls every minute, SQS, Lambda, EventBridge Pipe, `live-score`; cold starts accepted | 5.1, 5.2 |
| R2 Years of history, long jobs | Data, ML | Fargate ARM64 tasks under Step Functions `runTask.sync`; retries of 600 s; pinned revisions | 5.1, 5.2 |
| R3 SEC fair access | Constraint | Rate budget split between batch and live; batch egress through the NAT gateway; live Lambdas outside the VPC | 5.1, 5.4 |
| R4 No look-ahead, one feature implementation | ML | Same images on Fargate and Lambda; one Iceberg feature store | 5.1, 5.3 |
| R5 Upserts, reprocessing, at-least-once | Data | Iceberg on S3 with the Glue catalog; deterministic keys; SQS standard queues with DLQs and partial batch failures; `client_order_id` | 5.2, 5.3 |
| R6 A bad model never trades | ML, business | `save` vs `promote` in S3; `PROMOTE=never\|gated`; metadata as lineage | 5.3 |
| R7 $5 per month, on/off demo | Constraint | Serverless everywhere; `switched_on`; NAT only while on; S3 gateway endpoint; budget alarm | 5.4, 5.7 |
| R8 Secrets and trading keys | Security | SSM SecureString outside Terraform state; least-privilege roles; GitHub OIDC; approval-gated, encrypted two-stage deploy | 5.5, 5.6 |
| R9 Show the client | Business | CloudFront and a private S3 bucket with OAC; Basic auth at the edge; publisher Lambda writing static JSON; no public API | 5.9 |
| R10 Operate a multi-day backfill | Operations | Dashboard, DLQ alarms, `make status`; ECR retention vs pinned revisions; measure before scaling; one Iceberg writer | 5.8, 6 |

### 4.3 Terraform layout

All resources are in account `100118195151`, region `us-east-1`. Terraform state is in the bucket `secform4strategy-tfstate-<account>`, which `infra/bootstrap` creates, with S3 native locking (`use_lockfile = true`). The provider's `allowed_account_ids` rejects any other account. The root module `infra/envs/demo` calls ten modules:

| Module | Resources |
| --- | --- |
| `storage` | Data bucket, Glue database, Athena workgroup, one ECR repository per service, the monthly budget |
| `network` | VPC, two public and two private subnets, internet gateway, S3 gateway endpoint, security group, and the NAT gateway while switched on |
| `queues` | Five SQS queues, each with a dead-letter queue and a CloudWatch alarm, and an SNS topic |
| `iam` | The ECS execution role, one task role per service, one Lambda role per function |
| `ci` | The GitHub OIDC provider and the roles for images, plan and apply |
| `tasks` | The ECS cluster, one task definition and one log group per service |
| `retrain` | The `retrain` state machine and its weekly schedule |
| `live` | Six Lambda functions, the DynamoDB table `secform4strategy-seen`, the poller schedule, the `live-score` state machine and the EventBridge Pipe |
| `dashboard` | The CloudWatch dashboard |
| `site` | The site bucket, the CloudFront distribution and password function, and the publisher Lambda |

---

## 5. Architecture decisions by area

Each section opens with the requirements it serves. Each decision states the requirement that drives it, the alternative it was weighed against, and why it won.

### 5.1 Compute: batch and live

**Serves:** R1 speed, R2 long jobs, R3 SEC limit, R4 one feature implementation, R7 budget.

#### D1. Live path on Lambda

- **Driven by:** R1. A filing must be scored within minutes. Filings arrive in bursts, and most minutes bring none.
- **Decision:** six Lambda functions, one per service step, triggered by a schedule, SQS and Step Functions.
- **Alternative:** a long-running container (an ECS service) that polls and scores.
- **Why it won:** Lambda starts per event and bills per invocation, so an idle minute costs nothing. An always-on Fargate service would bill around the clock, against R7. The accepted cost is the cold start.

#### D2. Batch path on Fargate

- **Driven by:** R2. The 5-year load ran for days, a 10,000-row Iceberg upsert takes about 25 seconds, and training runs 128 fits in parallel. Lambda stops a run after 15 minutes and offers at most 10 GB of memory.
- **Decision:** one ECS task per step on Fargate, started and awaited by Step Functions.
- **Alternative:** Lambda for everything, or EC2 instances.
- **Why it won:** Fargate has no run-time limit, sizes each task separately, and bills only while the task runs. EC2 would add instances to patch and stop. Lambda cannot run the long steps at all.

#### D3. One image, two runtimes

- **Driven by:** R4. Training and live scoring must build features with the same code.
- **Decision:** each Lambda runs the service's own image with `<service directory>/.venv/bin/python -m awslambdaric`. `awslambdaric` is the Lambda runtime interface client: it receives events and calls the handler. Each handler sits beside the service's `main.py` and calls the same functions as the batch loop.
- **Alternative:** a separate live scorer that computes the features again.
- **Why it won:** one implementation leaves no skew between training and scoring. The cost is image size: about 1 GB, which lengthens cold starts.

#### D4. ARM64 (Graviton) everywhere

- **Driven by:** R7.
- **Decision:** CI builds `linux/arm64` images. Task definitions set `cpu_architecture = "ARM64"`, and the Lambdas set `architectures = ["arm64"]`.
- **Alternative:** x86_64.
- **Why it won:** AWS prices Fargate and Lambda on Graviton about 20% below x86 for the same vCPU and memory. The Python stack needs no x86-only binaries. Fargate ARM64 is not available in `use1-az3`, so the private subnets avoid that zone.

#### D5. Cold starts accepted

- **Driven by:** R1 against R7.
- **Decision:** no provisioned concurrency. A first cold start of a 1 GB image exceeds Lambda's 10-second initialization limit, so Lambda repeats the initialization inside the invocation, which then succeeds. Measured cold invocations were 10 seconds for scraper, 45 seconds for kafka-to-store and 39 seconds for yahoo-connect. Warm invocations take a few seconds.
- **Alternative:** provisioned concurrency, about $10 per month per function.
- **Why it won:** six warm functions would cost about $60 per month, twelve times the budget. A filing after a quiet period still reaches inference about 1 to 2 minutes after the poll, which keeps R1. Smaller images are the cheaper fix (Section 9).

#### Sizes

Task and function sizes follow measurements, not defaults.

| Service | Batch task (Fargate) | Live function (Lambda) |
| --- | --- | --- |
| master-index | 0.5 vCPU, 1 GB | 512 MB, 60 s, every minute |
| scraper | 0.5 vCPU, 1 GB, `FETCH_WORKERS=12` | 1024 MB, 120 s, batches of 10, at most 2 at a time |
| kafka-to-store | 2 vCPU, 8 GB: at 0.5 vCPU a 10,000-row batch took about 40 s, at 2 vCPU about 25 s | 2048 MB, 240 s, batches of up to 100, at most 2 at a time |
| yahoo-connect | 0.5 vCPU, 2 GB | 2048 MB, 180 s |
| target | 2 vCPU, 16 GB: 4 GB ran out of memory on one year | 2048 MB, 180 s |
| training | 4 vCPU, 16 GB for the parallel grid search | Not on the live path |
| inference | 0.5 vCPU, 2 GB (not a `retrain` step) | 2048 MB, 180 s |

The account allows 10 concurrent Lambda executions in total, shared by the live functions and the site publisher. The `maximum_concurrency` of 2 on each SQS trigger keeps the two queue consumers inside that quota. Section 9 describes the remaining risk.

### 5.2 Orchestration and messaging

**Serves:** R1 speed, R2 long jobs, R5 at-least-once, R10 recovery.

#### D6. Step Functions for the batch path

- **Driven by:** R2 and R7. Six steps must run in order, wait for long tasks, and retry.
- **Decision:** the state machine `secform4strategy-retrain` runs each step with `arn:aws:states:::ecs:runTask.sync`, which starts a Fargate task and waits for it to stop. A failed step is retried after 600 seconds, at most 3 times (`IntervalSeconds = 600`, `MaxAttempts = 3`, `BackoffRate = 1`).
- **Alternative:** Apache Airflow on Amazon MWAA.
- **Why it won:** Step Functions bills per state transition and costs nothing idle. MWAA runs an environment around the clock, far above $5 per month. Step Functions gives waits and retries without a scheduler server.

The retry interval is longer than the 300-second SQS visibility timeout on purpose. A rerun that started earlier would find the unfinished messages still hidden, stop at once and leave the work undone.

EventBridge Scheduler starts `retrain` every Saturday at 06:00 UTC (`cron(0 6 ? * SAT *)`). master-index then publishes only the filings of the last 7 days. A run's input can override environment variables per step, so the first full load is the same state machine with a different input:

```json
{"overrides": {"master-index": {"MODE": "historical", "YEARS": "5"}, "scraper": {"SEC_MAX_RATE": "8"}, "kafka-to-store": {"BUFFER_SIZE": "10000"}}}
```

#### D7. Revision pinning with `skip_destroy`

- **Driven by:** R10. A multi-day run must keep starting the same task definition while CI deploys new images.
- **Decision:** a run names exact task-definition revisions, for example `secform4strategy-kafka-to-store:3`. The task definitions set `skip_destroy = true`, so Terraform leaves the replaced revision active.
- **Alternative:** Terraform deregisters replaced revisions, its default.
- **Why it won:** ECS cannot start a task from an `INACTIVE` revision, so a deploy during a backfill would break the run. A pinned revision still needs its image in ECR (Section 6).

#### D8. The live chain: Scheduler, SQS, Lambda, Pipe, Step Functions

- **Driven by:** R1 and R5.
- **Decision:**
  1. EventBridge Scheduler invokes the master-index Lambda every minute (`rate(1 minute)`).
  2. master-index reads the first page of the SEC's current Form 4 feed. It sends the filings that the DynamoDB table `secform4strategy-seen` does not hold to `live-filings`, then records them, with a 7-day expiry.
  3. The scraper Lambda sends transactions to `live-transactions`.
  4. kafka-to-store upserts them into `BI4` and sends `{"tickers": [...]}` to `live-tickers`.
  5. The EventBridge Pipe `secform4strategy-live-tickers` starts one run of `live-score` per batch of up to 10 messages.
  6. `live-score` runs yahoo-connect, target and inference in order. Each step is retried twice on `States.TaskFailed`, after 5 and 10 seconds.
- **Alternative:** one Lambda that polls, scrapes, stores and scores in a single invocation.
- **Why it won:** each queue absorbs a burst and retries only its own failed messages. The two queue consumers scale separately. The Pipe starts the scoring sequence without glue code, and Step Functions shows each run's steps, which the website reads. A minute is the finest rate EventBridge Scheduler offers; the SEC feed does not push.

DynamoDB on demand holds the seen keys. It bills per request and costs nothing idle. Each item expires after 7 days (`SEEN_DAYS`), so the table stays small.

#### D9. SQS standard queues with dead-letter queues

- **Driven by:** R5. Every stage must survive a crash and a redelivery.
- **Decision:** five SQS standard queues, each with a dead-letter queue (DLQ).
- **Alternative:** SQS FIFO queues for exactly-once processing, or Amazon MSK (managed Kafka, as in the local stack).
- **Why it won:** every write is an upsert by key and every order carries `client_order_id`, so duplicates are harmless and FIFO ordering buys nothing. Standard queues cost less per request. MSK bills brokers around the clock.

| Setting | Value |
| --- | --- |
| Queues | `secform4strategy-filings`, `-transactions`, `-live-filings`, `-live-transactions`, `-live-tickers` |
| Dead-letter queues | The queue name plus `-dlq`, after 5 receives (`maxReceiveCount`) |
| Visibility timeout | 300 seconds |
| Retention | 4 days on the queues, 14 days on the DLQs |
| Encryption | SSE with SQS-managed keys |

The SQS triggers report partial batch failures. A handler returns the messages it did not finish in `batchItemFailures`, and SQS delivers only those again. The error contract of Section 3.3 maps onto these settings:

| Outcome | ECS consumer | Lambda handler | SQS result |
| --- | --- | --- | --- |
| Done | `ack` deletes the message | Not in `batchItemFailures` | Deleted |
| `RetryableError` | No ack | In `batchItemFailures` | Delivered again after 300 seconds |
| `PermanentError` | Logs it, then acks | Logs it, leaves it out | Deleted |
| Crash | No ack | The whole batch fails | Delivered again; after 5 receives, moved to the DLQ |

Retries therefore happen at three time scales: seconds in the adapter, minutes on the platform, and indefinitely in the DLQ with an alarm.

### 5.3 Storage: lakehouse and models

**Serves:** R4 one feature store, R5 upserts, R6 model risk, R7 budget.

#### D10. Iceberg on S3 with the Glue catalog

- **Driven by:** R5 (upserts by key, reprocessing), R4 (one store for both paths) and R7.
- **Decision:** Apache Iceberg tables in `s3://secform4strategy-data-<account>/warehouse/`. The AWS Glue Data Catalog records where each table's current metadata file is. Athena queries the tables with SQL.
- **Alternative:** a relational database (RDS), or a hosted feature store.
- **Why it won:** Iceberg supports upserts and schema changes on plain S3 objects, so it costs only storage while idle. RDS bills every hour it runs, and its smallest instance alone would exceed the budget. Batch tasks and live Lambdas read and write the same tables through the same catalog.

| Prefix | Content |
| --- | --- |
| `warehouse/` | The Iceberg tables; the location of the Glue database `secform4strategy` |
| `models/` | One folder per trained model, plus `latest.json`, which names the promoted model |
| `athena-results/` | Athena query results, expired after 7 days |

The bucket blocks all public access and encrypts objects with SSE-S3. Incomplete multipart uploads expire after 1 day. The Athena workgroup `secform4strategy` cancels any query that scans more than 1 GiB, so a careless query cannot break the budget.

Iceberg has one limit that shapes operations. pyiceberg uses optimistic concurrency: when two writers commit to one table, the second commit fails and redoes its upsert against the new snapshot. Parallel writers to one table therefore do not speed up a load (Section 6.2).

#### D11. Model store with a promotion gate

- **Driven by:** R6. A run on truncated data, a changed feature contract or a test run must never trade.
- **Decision:** training always **saves** a model to its own folder (`model.joblib` and `model.meta.json`). Only **promote** writes `latest.json`, and inference loads only what `latest.json` names. `PROMOTE` selects the behaviour:
  - `never`: save only. The local default, and the override for test and manual runs.
  - `gated`: promote when every gate check passes. The weekly `retrain` task definition sets it.
- **Alternative:** each training run replaces the live model, or a model registry service.
- **Why it won:** two S3 writes give versioning, promotion and rollback without another service. The gate requires at least 50,000 development rows and at least 80% of the current model's, the same feature-group version and features, holdout rows, a finite holdout MAE and at least one short. It does not compare MAE with the current model, because each model holds out a different period.

The metadata is the model's lineage. It records the training time, the git SHA, the scikit-learn version, the feature order, the parameters, the row counts, the holdout start and scores, the per-fold MAE and the threshold. inference refuses a model saved under another scikit-learn version, before it places any order. `scripts/promote_model.sh` lists the saved models and promotes one by hand, which is also the rollback. inference loads the model at each invocation, so a promotion takes effect at once.

### 5.4 Network

![AWS network](../diagrams/aws-network.drawio.svg)

*Figure 3: AWS network. Only Fargate needs the VPC. The tasks run in private subnets with no inbound access (R8). S3 traffic uses the free gateway endpoint, and all other outbound traffic uses the NAT gateway, which exists only while the stack is switched on (R7). Batch SEC traffic leaves from the NAT gateway's Elastic IP (R3).*

**Serves:** R3 SEC limit, R7 budget, R8 security.

| Resource | Value |
| --- | --- |
| VPC `secform4strategy` | `10.0.0.0/16`, DNS support and hostnames on |
| Public subnets | `10.0.0.0/24`, `10.0.1.0/24`; they hold only the NAT gateway |
| Private subnets | `10.0.10.0/24`, `10.0.11.0/24`, in `use1-az1` and `use1-az2` |
| Private route table | S3 to the gateway endpoint; `0.0.0.0/0` to the NAT gateway only while switched on |
| Security group `secform4strategy-tasks` | No inbound rules; all outbound allowed |
| NAT gateway and Elastic IP | In `secform4strategy-public-0`, only while `switched_on = true` |

#### D12. A VPC only for Fargate

- **Driven by:** R7 and R8. A VPC is required only where a service demands it.
- **Decision:** only the ECS tasks run in the VPC. Fargate runs a task only in `awsvpc` mode, with a network interface in a subnet and a security group.
- **Alternative:** put every compute resource in the VPC.

| Component | In the VPC | Reason |
| --- | --- | --- |
| ECS Fargate tasks | Yes | Fargate requires a subnet and a security group for every task |
| Live Lambda functions | No | They reach only the SEC, Yahoo Finance, Alpaca and AWS APIs. Outside the VPC they do not depend on the NAT gateway. |
| Site publisher Lambda | No | It reads only regional AWS services and internet APIs |
| Step Functions, SQS, DynamoDB, S3, EventBridge, CloudFront | Not applicable | Regional or global services, reached through endpoints and controlled by IAM |

- **Why it won:** nothing the Lambdas touch is inside the VPC, so a place in the VPC would protect nothing. It would make the live path depend on a NAT gateway that bills about $32 per month when always on. Nothing in the private subnets has a public IP, and the security group accepts no inbound connections. The Lambdas accept no inbound connections either: only their triggers invoke them, through the Lambda API and IAM.

#### D13. The SEC rate budget and egress

- **Driven by:** R3. Every SEC request carries one `User-Agent`, read from the secret `/secform4strategy/sec-user-agent`. Batch and live send the same `User-Agent`, so the limit of 10 requests per second applies to their sum.
- **Decision:** an explicit rate budget. `EdgarClient` spaces request starts at least `1 / SEC_MAX_RATE` seconds apart across its threads.

| Path | Cap | Egress |
| --- | --- | --- |
| Batch scraper | `SEC_MAX_RATE=5` | NAT gateway Elastic IP (fixed) |
| Live scrapers | 2 at a time × `SEC_MAX_RATE=2.25` = 4.5 | Addresses that Lambda assigns |
| Live poller | One feed request per minute (`SEC_MAX_RATE=1`) | Addresses that Lambda assigns |
| Full load | Batch raised to 8, with `live_enabled = false` | NAT gateway Elastic IP |

- **Alternative:** route the live Lambdas through the NAT gateway too, so all traffic leaves from one fixed address.
- **Why it won:** a single address would serve R3 only cosmetically, because the SEC already sees one `User-Agent`. It would cost R7: the live path would need the NAT gateway always on. The cost of the decision is that the SEC no longer sees one fixed address for all of the project's traffic. A 403 locks the client out for about 10 minutes, and requests during the lockout extend it. The adapter therefore raises `RetryableError` at once on a 403, and the platform retries minutes later.

#### D14. S3 gateway endpoint plus a switched NAT gateway

- **Driven by:** R7.
- **Decision:** a free S3 gateway endpoint on the private route table carries the Iceberg data, the models and the ECR image layers. The NAT gateway carries the internet traffic (SEC, Yahoo Finance, Alpaca) and the other AWS APIs (ECR API, SQS, Glue, SSM, DynamoDB, CloudWatch Logs). The NAT gateway exists only while switched on.
- **Alternative:** interface endpoints for the ECR API, ECR Docker, CloudWatch Logs, SQS and SSM, with no NAT gateway.
- **Why it won:** five interface endpoints in two availability zones cost about $73 per month (about $0.01 per hour per endpoint per zone), and the tasks need the internet for the SEC, Yahoo Finance and Alpaca anyway. The NAT gateway costs about $1.08 per day plus its Elastic IP at about $0.12 per day, and only while switched on. S3 traffic through the gateway endpoint adds no NAT data charges. While dormant, the private subnets reach only S3, and no task can run because it cannot pull its image or read its secrets.

### 5.5 Identity, secrets and security

**Serves:** R8 secrets and trading keys, R6 model risk.

#### D15. One role per service, built from capabilities

- **Driven by:** R8. A compromised service must reach only what it uses, and only one service may trade.
- **Decision:** the `iam` module creates one role per ECS task (`secform4strategy-<service>`) and one per Lambda (`secform4strategy-live-<service>`). `task_access` and `lambda_access` in `infra/envs/demo/main.tf` list, per service, the queues it sends to, the queues it receives from, and named capabilities. The module turns each entry into one inline policy.
- **Alternative:** one shared role for all services.
- **Why it won:** a shared role would give the scraper the Alpaca keys and training the right to place orders.

| Capability | Grants |
| --- | --- |
| Send to a queue | `sqs:GetQueueUrl`, `sqs:SendMessage` on that queue |
| Receive from a queue | `sqs:GetQueueUrl`, `sqs:GetQueueAttributes`, `sqs:ReceiveMessage`, `sqs:DeleteMessage` on that queue |
| `lakehouse-read` | Glue reads on the database and its tables; `s3:GetObject` on `warehouse/*` |
| `lakehouse-write` | `lakehouse-read` plus `glue:CreateTable`, `glue:UpdateTable` and `s3:PutObject` on `warehouse/*` |
| `models-read` / `models-write` | `s3:GetObject` / `s3:PutObject` on `models/*` |
| `seen-keys` | `dynamodb:BatchGetItem`, `dynamodb:BatchWriteItem` on `secform4strategy-seen` |
| `read-secrets` | `ssm:GetParameters` on `/secform4strategy/*` |
| `read-alpaca-keys` | `ssm:GetParameters` on the two Alpaca key parameters only |

For example, the scraper task can only receive from `filings` and send to `transactions`; it cannot touch the lakehouse. training can read the lakehouse and write models, but it cannot read secrets or place orders. Only inference and the site publisher can read the Alpaca keys. The orchestrators have their own roles: `secform4strategy-retrain` runs and stops the ECS tasks, the scheduler roles only start their one target, `secform4strategy-live-score` invokes only the three scoring Lambdas, and the Pipe role only reads `live-tickers` and starts `live-score`.

#### D16. Secrets in SSM, outside Terraform state

- **Driven by:** R8. The repository is public, and Terraform state stores every value it manages in plain text.
- **Decision:** the secrets are `SecureString` parameters in SSM Parameter Store: `/secform4strategy/sec-user-agent`, `/secform4strategy/alpaca-api-key` and `/secform4strategy/alpaca-api-secret`. The operator creates them with the AWS CLI, never in Terraform.
  - In ECS, the execution role reads them and injects them as environment variables, so the task roles hold no SSM permission.
  - Lambda cannot inject SSM parameters, so each live function reads its own at cold start: `SSM_PARAMETERS` names them, and `SsmSettings` reads them in one call.
- **Alternative:** Terraform reads the parameters into environment variables, or AWS Secrets Manager.
- **Why it won:** values that Terraform reads enter the state. SSM standard parameters are free; Secrets Manager costs $0.40 per secret per month and adds rotation the project does not use.

No container holds a static AWS key. ECS and Lambda give the code temporary credentials for its role, and boto3 finds them in its default credential chain.

### 5.6 CI/CD and deployment

![AWS CI/CD](../diagrams/aws-ci-cd.drawio.svg)

*Figure 4: CI/CD (R8). Three GitHub Actions workflows authenticate to AWS through OIDC. Master pushes images; pull requests get read-only plans; the approval-gated environment `aws` applies. The figure shows the deploy before it became two-stage: the plan role now also plans each master push, and the apply applies that saved, encrypted plan (see D18).*

**Serves:** R8 security, R10 operability.

| Workflow | Trigger | Does |
| --- | --- | --- |
| `quality.yml` | Pull requests and pushes to master | `make quality-static`, and `make type-check` as a separate job |
| `images.yml` | A successful `quality` run on a push to master | Builds a `linux/arm64` image for each service whose code changed, tags it with that code's last commit, and pushes it to ECR |
| `terraform.yml` | Pull requests and pushes to master that change `infra/` or `site/` | `terraform plan` on pull requests; on master, a saved plan of the merged commit, then an apply of exactly that plan after approval |

Terraform manages every AWS resource, including the ECR repositories and the CI roles. CI manages only the images and the runs of plan and apply. Each service has an ECR repository `secform4strategy/<service>`. Tags are immutable, ECR scans each image on push, and each repository keeps its 5 newest images (Chapter 6 explains why 5).

#### D17. GitHub OIDC instead of stored keys

- **Driven by:** R8.
- **Decision:** a workflow requests a token from `token.actions.githubusercontent.com`, and AWS STS exchanges it for temporary credentials of one role. Each role trusts the audience `sts.amazonaws.com` and exact `sub` claims.
- **Alternative:** an IAM user with access keys stored as GitHub secrets.
- **Why it won:** a stored key lives until someone rotates it, and anyone who reads it can use it. OIDC credentials are temporary and bound to one repository and one trigger.

| Role | Trusted subject | Permissions |
| --- | --- | --- |
| `secform4strategy-github-images` | `repo:josemita87/SecForm4Strategy:ref:refs/heads/master` | ECR push and pull on `secform4strategy/*` |
| `secform4strategy-github-plan` | `...:pull_request`, and `...:ref:refs/heads/master` for the plan of a master run | `ReadOnlyAccess`, plus write and delete on the state lock file only; `deny-site-keys` denies reading the site's key-value store |
| `secform4strategy-github-apply` | `...:environment:aws` | `AdministratorAccess` |

A pull request can assume only the plan role, so it cannot change any resource. Only a job in the GitHub environment `aws` can assume the apply role. That environment admits only master and requires a reviewer's approval. The branch rules of master require a pull request whose `quality-static` check passes, and they block force pushes and deletion.

#### D18. Two-stage, encrypted deploy

- **Driven by:** R8. The reviewer must approve exactly what is applied, and the repository's workflow artifacts are public.
- **Decision:** a merge to master that changes `infra/` or `site/` runs two jobs:
  1. `plan` runs `terraform plan -out=tfplan` on the merged commit with the read-only plan role and shows the plan on the run's summary page. It encrypts the saved plan (AES-256 with PBKDF2, key in the repository secret `TFPLAN_KEY`) and uploads only the encrypted file, kept for one day.
  2. `apply` waits for a reviewer's approval in the environment `aws`, decrypts the plan and runs `terraform apply tfplan`.

  A plan with no changes skips `apply`. If the state changed after the plan, Terraform refuses the saved plan and the run fails.
- **Alternative:** `terraform apply -auto-approve` after a fresh plan, and an unencrypted plan artifact.
- **Why it won:** a fresh plan after approval can differ from the plan the reviewer read. The saved plan holds sensitive values in plain text, and anyone signed in to GitHub can download the artifacts of a public repository.

One gap remains. Terraform reads the newest image of each repository when it plans, so a plan made before `images.yml` finishes deploys the previous images. Section 9 lists the fix.

### 5.7 Cost and switches

**Serves:** R7 budget.

| State | Cost | What is billed |
| --- | --- | --- |
| Dormant (`switched_on = false`) | About $0 per day | S3 and ECR storage. The VPC, subnets, gateway endpoint, IAM, Glue, idle queues, idle Lambdas, the on-demand DynamoDB table and the state machines cost nothing. |
| Switched on | About $1.20 per day, plus usage | The NAT gateway (about $1.08 per day) and its Elastic IP (about $0.12 per day). Usage: Fargate, Lambda, Step Functions, SQS and DynamoDB requests, NAT data processing, Athena scans, and the site publisher at about $1 per month. |

#### D19. Dormant by default, with three switches

- **Driven by:** R7. The demo runs for the presentation and the backfill, and sleeps otherwise.
- **Decision:** `infra/envs/demo/switch.auto.tfvars` holds three switches.

| Switch | Default | While `true` |
| --- | --- | --- |
| `switched_on` | `false` | The NAT gateway exists, the site publisher's schedule runs, and the two schedules below may run |
| `weekly_retrain_enabled` | `true` | The Saturday `retrain` schedule is enabled, if `switched_on` is `true` |
| `live_enabled` | `true` | The live poller schedule is enabled, if `switched_on` is `true` |

- **Alternative:** an always-on stack.
- **Why it won:** an always-on NAT gateway alone costs about $32 per month, more than six times the budget. Every other component is serverless and bills nothing idle, so one switch on the NAT gateway and the schedules covers almost all fixed cost.

`make switch-on` and `make switch-off` set `switched_on` on master, then commit and push. The change takes effect after a reviewer approves the Terraform apply, so the switch goes through the same reviewed path as any change. During a full load, `live_enabled` is off, because the batch scraper at 8 requests per second plus the live path's 4.5 would exceed the SEC's 10. `weekly_retrain_enabled` is also off, because the weekly run would use the same batch queues.

#### Serverless everywhere

| Need | Serverless choice | Server alternative avoided |
| --- | --- | --- |
| Compute | Fargate tasks, Lambda | EC2, an always-on ECS service |
| Orchestration | Step Functions, EventBridge Scheduler and Pipes | MWAA (Airflow) |
| Messaging | SQS | MSK (Kafka) |
| Tables | Iceberg on S3, Glue catalog, Athena | RDS |
| Seen keys | DynamoDB on demand | ElastiCache |
| Model serving | The model file in S3, loaded by inference | SageMaker endpoint |
| Website | CloudFront, S3, a publisher Lambda | A web server or API |

**Guardrails.** The budget `secform4strategy-monthly` is $5 per month. It emails `alert_email` when actual cost passes 50% and when forecast cost passes 100%. The Athena workgroup cancels any query above 1 GiB. Log groups keep 14 days.

### 5.8 Observability and operations

**Serves:** R10 operability, R5 failure handling.

#### D20. Alarms on dead letters, a free dashboard and one status command

- **Driven by:** R10. One person operates a multi-day run and a live path without an operations team.
- **Decision:**
  - **Dead-letter alarms.** Each DLQ has a CloudWatch alarm `secform4strategy-<queue>-dead-letters`. It goes into alarm when the DLQ holds at least one message, checked every 5 minutes, and publishes to the SNS topic `secform4strategy-queue-alerts`, which emails `alert_email`.
  - **Dashboard.** The CloudWatch dashboard `secform4strategy` shows queue depths, messages processed per minute and the backlog ETA of `filings` and `transactions`; DLQ depths; `retrain` and `live-score` runs; the live Lambdas' invocations, errors and p90 duration; and `ERROR` log lines per 15 minutes. A dashboard with at most 50 metrics is free.
  - **`make status`.** One read-only command prints the stack state in about 15 seconds: whether the NAT gateway exists and the schedules are enabled, each running `retrain` step and its attempt, per-queue depth, rate and ETA, the running ECS tasks, the `ERROR` log lines of the last hour, the `live-score` outcomes of the last 24 hours, and the promoted model folder.
  - **Container Insights** on the ECS cluster shows each task's CPU and memory use.
- **Alternative:** a third-party monitoring service, or X-Ray tracing.
- **Why it won:** CloudWatch is already where the logs and metrics are, and the dashboard and alarms stay inside the free tier. A DLQ is the one place a message ends up after every retry fails, so one alarm per DLQ covers every lost message.

Every service logs one line per batch, for example `10 of 10 filings -> 23 transactions` (scraper).

### 5.9 The study website

**Serves:** R9 show the client, R8 security, R7 budget.

The site is a password-protected study website with five pages:

| Page | Shows |
| --- | --- |
| Story | The idea, the data, the model and the results |
| Live | The latest `live-score` runs, their filings, predictions and orders |
| Portfolio | The Alpaca paper account: equity, positions and orders |
| Model | The current model: parameters, validation folds, holdout results and feature importance |
| Explore | The largest insider sales and the clusters of insider sales of the last 7 days |

The site is deployed (2026-11-22): CloudFront serves it behind the password, and the publisher writes the data files every minute.

![Site architecture](../diagrams/site-architecture.drawio.svg)

*Figure 5: Site architecture (R9). CloudFront serves static files from a private S3 bucket after a CloudFront Function checks the password. A publisher Lambda runs every minute and after each `live-score` run. It reads the system's state and writes `data/*.json` into the bucket, so the browser never reaches the system itself (R8).*

#### D21. A static site with a publisher, no public API

- **Driven by:** R9 and R8. A client must see live runs and the portfolio, and the internet must not reach the lakehouse or the trading keys.
- **Decision:** the Lambda `secform4strategy-site-publisher` reads the lakehouse, the models, Step Functions, SQS, CloudWatch and Alpaca, and writes five JSON files under `data/` in the bucket `secform4strategy-site-<account>`. The browser fetches only static files and `/data/<name>.json` through CloudFront.
- **Alternative:** a dynamic site with an API (API Gateway and a Lambda) that queries the system per request.
- **Why it won:** an API accepts requests from the internet and runs code for each one, and each request could read the lakehouse or call Alpaca. Static files expose only what the publisher already wrote: no keys, no credentials and no write access. If the password leaks, the reader sees paper-account figures, tickers and model results, and gets no access to AWS. A static site also costs almost nothing while nobody visits it.

| File | Written | Contents |
| --- | --- | --- |
| `data/live.json` | After each `live-score` run, and every minute | The latest 50 runs, with steps, filings, predictions and orders |
| `data/health.json` | Every minute | Switches, queue depths, Lambda metrics, run outcomes, the model version |
| `data/portfolio.json` | Every minute | The paper account, equity history, positions and latest 100 orders |
| `data/model.json` | When the model version changes | Parameters, folds, holdout metrics, cumulative return against SPY, feature importance |
| `data/explore.json` | Every 15 minutes | The largest insider sales and the insider clusters of the last 7 days |

Two triggers invoke the publisher asynchronously: the schedule `secform4strategy-site-publish` (`rate(1 minute)`, `{"mode": "all"}`, only while switched on) and the EventBridge rule `secform4strategy-site-live-score-finished` (`{"mode": "live"}`, when a `live-score` run ends). Lambda drops an event older than 60 seconds and retries no failed invocation, so a slow publish does not queue more behind it. The next minute's publish replaces a missed one.

#### D22. CloudFront and a private bucket with origin access control

- **Driven by:** R8 and R7.
- **Decision:** the bucket blocks all public access. The CloudFront distribution reads it through an origin access control (OAC), and the bucket policy admits only this distribution.

| Resource | Settings |
| --- | --- |
| Distribution | HTTPS on the default `*.cloudfront.net` certificate (HTTP redirects to HTTPS), `PriceClass_100`, gzip and brotli, `GET` and `HEAD` only |
| Cache of `data/*` | At most 5 seconds (`secform4strategy-site-data`) |
| Cache of other files | At most 60 seconds (`secform4strategy-site-static`) |
| Response headers | `Managed-SecurityHeadersPolicy`: HSTS, `X-Frame-Options`, `nosniff`, referrer policy |

- **Alternative:** S3 static website hosting with a public bucket.
- **Why it won:** S3 website hosting serves only HTTP and needs a public bucket. CloudFront adds HTTPS, edge caching and a place to check the password. The site costs a few cents per month for S3 and stays inside the CloudFront free tier.

#### D23. Basic auth at the edge with a hashed credential

- **Driven by:** R9 (the site is for the client and the course, not the public) and R8.
- **Decision:** the CloudFront Function `secform4strategy-site-auth` runs on every request, before CloudFront reads its cache. It computes the SHA-256 hash of the browser's `authorization` header and compares it with the key `credential_sha256` in the CloudFront KeyValueStore `secform4strategy-site-auth`. On a mismatch it returns 401 with `www-authenticate: Basic realm="SecForm4Strategy"`. Terraform creates the store empty and never reads or writes its keys. `scripts/set_site_password.sh` writes only the hash. Until the key is set, the site refuses every request.
- **Alternative:** Amazon Cognito, or a password in Terraform.
- **Why it won:** Basic auth at the edge needs no server and no user pool for one shared password. The hash stays out of the code and the Terraform state, and the plan role is denied `GetKey` and `ListKeys` on the store.

The publisher role `secform4strategy-live-site-publisher` can read run histories, queue attributes, metrics, schedules, the lakehouse and the models, read the two Alpaca key parameters, and write only `data/*` in the site bucket. It cannot start or stop runs, change queues or schedules, write tables, write models or write the static files. It uses the same Alpaca key pair as inference, which can place and cancel orders. The publisher calls only read endpoints, so that isolation relies on code, not on permissions.

![Site security](../diagrams/site-security.drawio.svg)

*Figure 6: Site security in five layers (R8): edge (HTTPS, password, security headers), origin (private bucket, OAC), identities (least-privilege roles), secrets (SSM and a hash in a key-value store) and no public API (R9).*

---

## 6. Operations: the 5-year backfill

### 6.1 The run

`backfill-5y` is the first full load of five years of filings: a run of the `retrain` state machine with the full-load input of D6, started by hand on 2026-11-20 at 18:11 UTC.

| Step | Outcome |
| --- | --- |
| master-index | Finished. It published the filings of five years to `secform4strategy-filings`. |
| scraper | Processed every filing. At the end, the `filings` queue and its DLQ were empty. The task exited non-zero, as designed, because it had left filings for a retry. |
| scraper retries | Each retry failed with `CannotPullContainerError`. The run ended `FAILED`; the last four steps did not start. |

The cause was a missing image. The run had pinned the revision `scraper:3` (D7), which named an image digest. The ECR lifecycle rule then kept only 2 images per repository, and newer scraper images had been pushed during the run, so ECR had already deleted that image. `skip_destroy` kept the revision active, but a revision without its image cannot start a task.

The scraper's work was not lost. Its transactions were in `secform4strategy-transactions`, which keeps messages for 4 days, and every step is an upsert by key. The remaining steps run as ECS tasks started by hand, on the latest revisions: kafka-to-store (2 vCPU, 8 GB, `BUFFER_SIZE` raised to 30,000), yahoo-connect, target, and training with `PROMOTE=never`. The saved model is then promoted by hand with `scripts/promote_model.sh` (D11).

### 6.2 Measurements

| Measurement | Value |
| --- | --- |
| scraper rate, `SEC_MAX_RATE=8`, `FETCH_WORKERS=12` | 6 to 7.8 filings per second |
| kafka-to-store, one 10,000-row batch at 0.5 vCPU / 2 vCPU | About 40 / 25 seconds |
| kafka-to-store, one 30,000-row batch at 2 vCPU | About 75 seconds |
| Size of the Iceberg warehouse | About 430 MB |

Parallel SQS calls did not change the kafka-to-store rate much, so most of a batch is spent after SQS. Four times the CPU cut a 10,000-row batch from 40 to 25 seconds, because the task went from half a core to a full core. Three times the batch took three times as long, so the cost is per row, at about 400 rows per second: the pandas cleaning and pyiceberg's upsert merge, on one core. More tasks would not help either, because parallel Iceberg commits conflict (D10).

### 6.3 Lessons

1. **A pinned revision depends on its image.** `skip_destroy` keeps old revisions active, but ECR can still delete their images, so each repository now keeps 5. During a multi-day run, push at most four new images of a service whose step has not started.
2. **Incremental steps make a failed run recoverable.** Upserts by deterministic key (R5) and a 4-day queue retention let the remaining steps run by hand, with no work repeated and no duplicate rows.
3. **Measure before scaling.** One full core helps; more tasks, more cores and bigger batches do not. The next step is profiling one batch, then an append-then-deduplicate write for full loads. Container Insights now shows each task's CPU and memory use.

---

## 7. Results in brief

The results show that the system works end to end. They do not yet settle the hypothesis.

### 7.1 Model on one year of data

The 1-year results come from a walk-forward run of the merged training code on 2026-11-21: 73,195 labelled rows, 53,747 development rows after purging, and 14,639 holdout rows from 2026-06-09. The 128 fits plus the refit used about 2,200 CPU-seconds.

| Predictor | Holdout MAE, 1-year | Holdout MAE, 5-year |
| --- | --- | --- |
| `model` | 0.1216 | **[5-year results: to be filled after the Sunday 2026-11-22 run]** |
| `zero` (no move) | 0.1193 | **[5-year results: to be filled after the Sunday 2026-11-22 run]** |
| `train_mean` | 0.1218 | **[5-year results: to be filled after the Sunday 2026-11-22 run]** |
| `ticker_history` | 0.1421 | **[5-year results: to be filled after the Sunday 2026-11-22 run]** |

| Threshold | Holdout shorts, 1-year | Mean return per short, 1-year | 5-year |
| --- | --- | --- | --- |
| 0 | 7,067 | +1.8% | **[5-year results: to be filled after the Sunday 2026-11-22 run]** |
| -2% | 1,942 | +4.0% | **[5-year results: to be filled after the Sunday 2026-11-22 run]** |
| -5% (traded) | 265 | +10.8% | **[5-year results: to be filled after the Sunday 2026-11-22 run]** |
| -10% | 29 | Too few to read | **[5-year results: to be filled after the Sunday 2026-11-22 run]** |

The model does not beat the zero baseline on MAE, on the holdout or on any of the four validation folds. Its most negative predictions still rank well: a positive mean return per short means the shorted stocks fell on average. The 265 trades at -5% come from one holdout window, and the figures exclude trading costs, borrow fees and overlapping positions. This is a signal worth testing, not a result.

### 7.2 Live path verification

The live path ran end to end on real filings. Filings for AMZN, TGT, DGICA and IMTX went from the SEC feed through the poller, scraper and kafka-to-store to the `live-score` state machine, and inference scored them. AWS records seven successful `live-score` runs. Alpaca paper orders were placed and then cancelled, to verify three things: the broker wiring, the `client_order_id` idempotency and the IAM path to the Alpaca keys. The measured cold starts of D5 come from these runs.

### 7.3 Batch path verification

No `retrain` run has finished all six steps on AWS. The 1-year runs were stopped by hand. The 5-year backfill scraped every filing, then ended `FAILED`, and its remaining steps run by hand (Chapter 6). The 5-year results are **[5-year results: to be filled after the Sunday 2026-11-22 run]**.

---

## 8. Design decisions and trade-offs

The table collects the decisions of Chapter 5, with the requirement behind each.

| Decision | Req. | Rejected alternative | Reason |
| --- | --- | --- | --- |
| Live path on Lambda (D1) | R1, R7 | An always-on container | Starts per event, bills per invocation |
| Batch path on Fargate under Step Functions (D2, D6) | R2, R7 | Lambda only; Airflow on MWAA | Lambda stops after 15 minutes; MWAA bills around the clock |
| Same images on Fargate and Lambda (D3) | R4 | A separate live scorer | One feature implementation, no training-serving skew |
| ARM64 (Graviton) (D4) | R7 | x86_64 | About 20% cheaper |
| Cold starts accepted (D5) | R1, R7 | Provisioned concurrency | About $10 per month per function; cold filings still scored in 1 to 2 minutes |
| Retry after 600 s; `skip_destroy`; ECR keeps 5 images (D6, D7) | R10 | Immediate retry; deregistered revisions; 2 images | A retry must outlast the visibility timeout, and a pinned revision needs its image |
| SQS standard queues, DLQs, partial batch failures (D9) | R5 | SQS FIFO; MSK | Upserts by key and `client_order_id` make duplicates harmless; MSK bills brokers around the clock |
| Iceberg on S3 with the Glue catalog (D10) | R4, R5, R7 | RDS; a hosted feature store | Upserts on plain objects; only storage is billed while idle |
| One Iceberg writer per table (Ch. 6) | R10 | Parallel kafka-to-store tasks | Parallel commits conflict and redo their upserts |
| Promotion gate; `save` vs `promote` (D11) | R6 | Each run replaces the live model | A truncated dataset or changed features would reach inference at once |
| Model file in S3, no endpoint | R7 | SageMaker endpoint | A small pipeline; an endpoint bills every hour |
| VPC only for Fargate (D12) | R7, R8 | Everything in the VPC | The Lambdas reach nothing inside the VPC and would depend on the NAT gateway |
| Rate budget; live egress from Lambda addresses (D13) | R3, R7 | All SEC traffic through one NAT address | The SEC already sees one `User-Agent`; the budget keeps the sum under 10 |
| S3 gateway endpoint plus a switched NAT gateway (D14) | R7 | Five interface endpoints | About $73 per month, and the tasks need the internet anyway |
| Dormant by default; three switches (D19) | R7 | Always-on stack | An always-on NAT gateway costs about $32 per month |
| Secrets in SSM, created outside Terraform (D16) | R8 | Terraform-managed secrets; Secrets Manager | Values Terraform reads enter the state; SSM standard parameters are free |
| One role per service (D15) | R8 | One shared role | Only inference and the publisher can read the Alpaca keys |
| OIDC roles for CI (D17) | R8 | IAM user keys in GitHub | Temporary credentials, bound to one repository and trigger |
| Two-stage deploy, encrypted saved plan (D18) | R8 | `apply -auto-approve`; a plain artifact | The reviewer approves what is applied; public artifacts expose nothing |
| Static site with a publisher; CloudFront with OAC; edge Basic auth (D21 to D23) | R8, R9 | An API; a public bucket; Cognito | No code runs per internet request, and no server is needed |
| `FARGATE` On-Demand | R2, R10 | Fargate Spot, about 70% cheaper | An interruption fails the step, and 3 retries may not cover a multi-day run |

---

## 9. Limitations and future work

### 9.1 Cloud

| Follow-up | Benefit |
| --- | --- |
| Fargate Spot for scraper and kafka-to-store, with an On-Demand fallback in a Step Functions `Catch` | About 70% cheaper tasks for the two longest steps. An interruption falls back to an On-Demand task instead of using up the retries. |
| Iceberg maintenance: expire snapshots, compact data files | Every commit adds a snapshot and small files. Athena `OPTIMIZE` and `VACUUM` fit under the 1 GiB scan limit, because the warehouse is about 430 MB. |
| Append-then-deduplicate write for full loads | kafka-to-store appends each batch without matching keys against the whole table, and one pass removes duplicates at the end. A full load then no longer slows as the table grows. |
| Make the Terraform apply wait for the `images` workflow | A plan made before the build ends deploys the previous image. |
| Smaller images for the live Lambdas | The about 1 GB images cause cold starts of up to 45 seconds. |
| A Lambda concurrency quota above 1,000 | The account allows 10 concurrent executions, shared by the live functions and the publisher, so a burst of filings can throttle a `live-score` step, which fails its run after two retries. A quota request must ask for more than 1,000. Reserved concurrency for the scoring steps then becomes possible. |
| Custom domain with an ACM certificate | Names the project and allows HSTS preload; about $15 per year. |
| AWS WAF rate-based rule on the site | Limits password guessing; about $6 per month. |
| A Content-Security-Policy response header | `Managed-SecurityHeadersPolicy` sends none, so the browser does not restrict where scripts and styles come from. It needs testing against the inline styles that Plotly adds. |

### 9.2 Model

The model does not beat the zero baseline on MAE, and the +10.8% at -5% rests on 265 trades in one window, without costs. Next: rerun on 5 years, add costs and a benchmark (SPY), and test several holdout windows.

---

## 10. Conclusion

SecForm4Strategy turns one business idea into a set of explicit cloud requirements, and each AWS decision traces back to one of them. Speed from disclosure gives a separate live path on Lambda, SQS, an EventBridge Pipe and Step Functions. Years of history give a batch path on Fargate under Step Functions. The ML requirement of one feature implementation gives the same images on both paths and one Iceberg lakehouse on S3. Model risk gives a promotion gate. The SEC limit gives an explicit rate budget, and the budget gives a stack that is serverless everywhere and switched off by default.

Security follows the same method: SSM secrets outside the state, one role per service, OIDC for CI, an encrypted plan applied only after approval, and a static website with no public API.

The live path ran end to end on real filings. The 5-year backfill exercised the batch path at scale and taught two lessons: keep the images of pinned revisions, and measure before scaling. On one year of data the model does not beat a zero prediction on MAE; only its strongest calls look promising. The infrastructure is the deliverable: it can rerun the test on five years of data, add costs and a benchmark, and keep scoring live filings, so the hypothesis can be accepted or rejected on evidence.

---

## Appendix A: Resource inventory

`<account>` stands for the AWS account ID `100118195151`.

| Area | Resources |
| --- | --- |
| State | S3 bucket `secform4strategy-tfstate-<account>` |
| Storage | S3 bucket `secform4strategy-data-<account>` (`warehouse/`, `models/`, `athena-results/`); Glue database and Athena workgroup `secform4strategy` (1 GiB scan limit); ECR repositories `secform4strategy/<service>` for master-index, scraper, kafka-to-store, yahoo-connect, target, training, inference, site-publisher |
| Network | VPC `secform4strategy` (`10.0.0.0/16`); subnets `secform4strategy-public-0`, `-1`, `secform4strategy-private-0`, `-1`; security group `secform4strategy-tasks`; S3 gateway endpoint; NAT gateway and Elastic IP while switched on |
| Compute | ECS cluster `secform4strategy`; task definitions `secform4strategy-<service>`; Lambda functions `secform4strategy-live-<service>` (6) and `secform4strategy-site-publisher` |
| Orchestration | State machines `secform4strategy-retrain`, `secform4strategy-live-score`; schedules `secform4strategy-weekly-retrain`, `-live-poll`, `-site-publish`; EventBridge Pipe `secform4strategy-live-tickers`; EventBridge rule `secform4strategy-site-live-score-finished` |
| Messaging | SQS queues `secform4strategy-filings`, `-transactions`, `-live-filings`, `-live-transactions`, `-live-tickers`, each with `-dlq`; SNS topic `secform4strategy-queue-alerts` |
| Data | DynamoDB table `secform4strategy-seen` |
| Identity | ECS roles `secform4strategy-execution`, `secform4strategy-<service>`; Lambda roles `secform4strategy-live-<service>`, `secform4strategy-live-site-publisher`; orchestration roles `secform4strategy-retrain`, `-retrain-scheduler`, `-live-scheduler`, `-live-score`, `-live-tickers-pipe`, `-site-scheduler`; CI roles `secform4strategy-github-images`, `-github-plan`, `-github-apply` |
| Secrets | SSM parameters `/secform4strategy/sec-user-agent`, `/secform4strategy/alpaca-api-key`, `/secform4strategy/alpaca-api-secret` |
| Monitoring | Dashboard `secform4strategy`; alarms `secform4strategy-<queue>-dead-letters` (5); log groups `/secform4strategy/<service>`, `/aws/lambda/secform4strategy-live-<service>`, `/aws/lambda/secform4strategy-site-publisher` |
| Cost | Budget `secform4strategy-monthly` ($5) |
| Site | S3 bucket `secform4strategy-site-<account>`; CloudFront distribution; CloudFront Function and KeyValueStore `secform4strategy-site-auth`; cache policies `secform4strategy-site-static`, `secform4strategy-site-data` |

## Appendix B: Glossary

| Term | Meaning |
| --- | --- |
| Form 4 | The SEC form an insider files within two business days of a trade in the company's stock |
| EDGAR | The SEC's public filing system |
| `accepted_at` | The moment the SEC accepted a filing and made it public |
| Look-ahead bias | Use of information that was not public at decision time |
| Holdout | The latest 20% of rows, scored once |
| Promotion | Making a saved model version the one inference loads, by writing `latest.json` |
| MAE | Mean absolute error |
| Short | Selling borrowed shares, to profit when the price falls |
| Paper trading | Trading with simulated money on a broker's test account |
| Apache Iceberg | An open table format of data and metadata files on object storage |
| Upsert | Insert a row, or update it if a row with the same key exists |
| Fargate | ECS mode in which AWS runs containers without servers to manage |
| Fargate Spot | Fargate capacity at a discount, which AWS can interrupt at short notice |
| Graviton | AWS's ARM64 processors |
| Step Functions | AWS service that runs state machines: ordered steps with retries |
| `runTask.sync` | The Step Functions integration that starts an ECS task and waits for it to stop |
| EventBridge Pipe | A managed link from a source (here an SQS queue) to a target (here a state machine) |
| Visibility timeout | How long SQS hides a received message before it delivers it again |
| Dead-letter queue (DLQ) | A queue that receives messages that failed too often |
| Partial batch failure | A Lambda response that lists only the failed messages of an SQS batch, so SQS redelivers only those |
| NAT gateway | Gives private subnets outbound-only internet access |
| S3 gateway endpoint | A free route from private subnets to S3 that bypasses the NAT gateway |
| Interface endpoint | A billed private network interface for one AWS service API in one subnet |
| OAC | Origin access control: lets only one CloudFront distribution read a private S3 bucket |
| OIDC | OpenID Connect; GitHub Actions uses it to get temporary AWS credentials without a stored key |
| Cold start | The first invocation of a Lambda after idle time, which loads the image and runs the imports |
| `switched_on` | The Terraform variable that creates the NAT gateway and enables the schedules |
