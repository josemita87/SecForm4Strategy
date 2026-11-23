terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.71"
    }
  }
  backend "s3" {
    bucket       = "secform4strategy-tfstate-100118195151"
    key          = "envs/demo/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region              = "us-east-1"
  allowed_account_ids = ["100118195151"]
  default_tags {
    tags = { Project = "secform4strategy", Environment = "demo", ManagedBy = "terraform" }
  }
}

module "storage" {
  source             = "../../modules/storage"
  name               = "secform4strategy"
  monthly_budget_usd = var.monthly_budget_usd
  alert_email        = var.alert_email
  services = [
    "master-index", "scraper", "kafka-to-store",
    "yahoo-connect", "target", "training", "inference", "site-publisher"
  ]
}

output "storage" {
  value = module.storage
}

module "network" {
  source      = "../../modules/network"
  name        = "secform4strategy"
  nat_enabled = var.switched_on
}

output "network" {
  value = module.network
}

module "queues" {
  source      = "../../modules/queues"
  name        = "secform4strategy"
  queues      = ["filings", "transactions", "live-filings", "live-transactions", "live-tickers"]
  alert_email = var.alert_email
}

output "queues" {
  value = module.queues
}

module "iam" {
  source        = "../../modules/iam"
  name          = "secform4strategy"
  data_bucket   = module.storage.data_bucket
  glue_database = module.storage.glue_database
  queue_arns    = { for key, queue in module.queues.queues : key => queue.arn }
  task_access = {
    master-index   = { sends = ["filings"] }
    scraper        = { receives = ["filings"], sends = ["transactions"] }
    kafka-to-store = { receives = ["transactions"], capabilities = ["lakehouse-write"] }
    yahoo-connect  = { capabilities = ["lakehouse-write"] }
    target         = { capabilities = ["lakehouse-write"] }
    training       = { capabilities = ["lakehouse-read", "models-read", "models-write"] }
    inference      = { capabilities = ["lakehouse-read", "models-read"] }
  }
  lambda_access = {
    master-index   = { sends = ["live-filings"], capabilities = ["seen-keys", "read-secrets"] }
    scraper        = { receives = ["live-filings"], sends = ["live-transactions"], capabilities = ["read-secrets"] }
    kafka-to-store = { receives = ["live-transactions"], sends = ["live-tickers"], capabilities = ["lakehouse-write"] }
    yahoo-connect  = { capabilities = ["lakehouse-write"] }
    target         = { capabilities = ["lakehouse-write"] }
    inference      = { capabilities = ["lakehouse-read", "models-read", "read-alpaca-keys"] }
    site-publisher = { capabilities = ["lakehouse-read", "models-read", "read-alpaca-keys"] }
  }
}

output "iam" {
  value = module.iam
}

module "ci" {
  source            = "../../modules/ci"
  name              = "secform4strategy"
  github_repository = "josemita87/SecForm4Strategy"
  state_bucket      = "secform4strategy-tfstate-100118195151"
}

output "ci" {
  value = module.ci
}

locals {
  region = "us-east-1"
  roles  = module.iam.task_role_arns
  queues = { for key, queue in module.queues.queues : key => queue.name }
  sqs    = { QUEUE__BACKEND = "sqs", QUEUE__REGION = local.region }
  lakehouse = {
    PYICEBERG_CATALOG__DEFAULT__TYPE         = "glue"
    PYICEBERG_CATALOG__DEFAULT__GLUE__REGION = local.region
    PYICEBERG_CATALOG__DEFAULT__S3__REGION   = local.region
    FEATURE_GROUP_VERSION                    = "1"
  }
  models = {
    MODEL_STORE__BACKEND = "s3"
    MODEL_STORE__REGION  = local.region
    MODEL_STORE__BUCKET  = module.storage.data_bucket
  }
  training_run = { SYSTEM_TRAINING = "true", FILTER_KEY = "acquired_disposed", ACQUIRED_DISPOSED = "D" }
}

module "tasks" {
  source             = "../../modules/tasks"
  name               = "secform4strategy"
  execution_role_arn = module.iam.execution_role_arn
  tasks = {
    master-index = {
      task_role_arn = local.roles["master-index"]
      environment   = merge(local.sqs, { OUTPUT_QUEUE = local.queues.filings, BUFFER_SIZE = "10000", MODE = "recent", DAYS_BACK = "7" })
      secrets       = { SEC_USER_AGENT = "sec-user-agent" }
    }
    scraper = {
      task_role_arn = local.roles.scraper
      environment = merge(local.sqs, {
        INPUT_QUEUE   = local.queues.filings, OUTPUT_QUEUE = local.queues.transactions
        BUFFER_SIZE   = "200", POLL_TIMEOUT = "20", DELAY = "0", MODE = "last_quarter", EXIT_WHEN_EMPTY = "true"
        FETCH_WORKERS = "12", RETRY_PAUSE = "5", SEC_MAX_RATE = "5"
      })
      secrets = { SEC_USER_AGENT = "sec-user-agent" }
    }
    kafka-to-store = {
      task_role_arn = local.roles["kafka-to-store"]
      # An Iceberg upsert loads and merges table rows; 0.5 vCPU / 2 GB made each 10,000-row batch CPU-bound
      cpu    = 2048
      memory = 8192
      environment = merge(local.sqs, local.lakehouse, {
        INPUT_QUEUE = local.queues.transactions, BUFFER_SIZE = "100", POLL_TIMEOUT = "20", SYSTEM_TRAINING = "true"
      })
    }
    yahoo-connect = {
      task_role_arn = local.roles["yahoo-connect"]
      memory        = 2048
      environment   = merge(local.lakehouse, local.training_run, { BUFFER_SIZE = "50" })
    }
    # Reads every ticker's price history at once: 4 GB ran out of memory on one year of filings
    target = {
      task_role_arn = local.roles.target
      cpu           = 2048
      memory        = 16384
      environment   = merge(local.lakehouse, local.training_run, { DELTA_PERIOD = "30" })
    }
    # Grid search fits 128 models in parallel (n_jobs=-1) on ~400k rows from the 5-year backfill
    training = {
      task_role_arn = local.roles.training
      cpu           = 4096
      memory        = 16384
      environment = merge(local.lakehouse, local.models, {
        HOLDOUT_RATIO = "0.2", N_FOLDS = "4"
        RANDOM_STATE  = "42", TARGET_FEATURE = "pct_change"
        # Scheduled runs promote through the gate; an ad-hoc run overrides PROMOTE to "never" (docs/training.md#promotion)
        PROMOTE = "gated", MIN_TRAIN_ROWS = "50000", MIN_ROWS_RATIO = "0.8"
      })
    }
    inference = {
      task_role_arn = local.roles.inference
      memory        = 2048
      environment = merge(local.lakehouse, local.models, {
        ALPACA_BASE_URL = "https://paper-api.alpaca.markets", QTY = "1"
      })
      secrets = { ALPACA_API_KEY = "alpaca-api-key", ALPACA_API_SECRET = "alpaca-api-secret" }
    }
  }
}

output "tasks" {
  value = module.tasks
}

module "retrain" {
  source               = "../../modules/retrain"
  name                 = "secform4strategy"
  cluster_arn          = module.tasks.cluster_arn
  task_definition_arns = module.tasks.task_definition_arns
  pass_role_arns       = concat([module.iam.execution_role_arn], values(module.iam.task_role_arns))
  subnet_ids           = module.network.private_subnet_ids
  security_group_id    = module.network.security_group_id
  schedule_enabled     = var.switched_on && var.weekly_retrain_enabled
  # Every step is idempotent (upserts by key, SQS redelivery), so a reclaimed Spot task only repeats its batch
  spot_steps = ["master-index", "scraper", "kafka-to-store", "yahoo-connect", "target", "training"]
}

output "retrain" {
  value = module.retrain
}

locals {
  inference_run = { SYSTEM_INFERENCE = "true", FILTER_KEY = "acquired_disposed", ACQUIRED_DISPOSED = "D" }
  # Lambda cannot inject SSM parameters; SsmSettings reads the ones SSM_PARAMETERS names at cold start
  sec_secrets    = { SSM_PARAMETERS = jsonencode({ sec_user_agent = "/secform4strategy/sec-user-agent" }) }
  alpaca_secrets = jsonencode({ alpaca_api_key = "/secform4strategy/alpaca-api-key", alpaca_api_secret = "/secform4strategy/alpaca-api-secret" })
}

module "live" {
  source = "../../modules/live"
  name   = "secform4strategy"
  poller = "master-index"
  # The live chain runs outside the VPC, so it needs no NAT gateway and no switched_on
  schedule_enabled  = var.live_enabled
  score_steps       = ["yahoo-connect", "target", "inference"]
  tickers_queue_arn = module.queues.queues["live-tickers"].arn
  functions = {
    master-index = {
      role_arn  = module.iam.lambda_role_arns["master-index"]
      directory = "/repo/sec-feature-pipeline/master-index"
      handler   = "handler.handle"
      memory    = 512
      timeout   = 60
      environment = merge(local.sqs, local.sec_secrets, {
        OUTPUT_QUEUE = local.queues["live-filings"], BUFFER_SIZE = "100", MODE = "live"
        SEEN_TABLE   = "secform4strategy-seen", SEEN_DAYS = "7", SEC_MAX_RATE = "1"
      })
    }
    # Two at a time at 2.25 requests per second: 4.5 of SEC's 10, beside the batch scraper's 5
    scraper = {
      role_arn  = module.iam.lambda_role_arns.scraper
      directory = "/repo/sec-feature-pipeline/scraper"
      handler   = "handler.handle"
      environment = merge(local.sqs, local.sec_secrets, {
        INPUT_QUEUE = local.queues["live-filings"], OUTPUT_QUEUE = local.queues["live-transactions"]
        BUFFER_SIZE = "10", POLL_TIMEOUT = "0", DELAY = "0", MODE = "live", FETCH_WORKERS = "4", SEC_MAX_RATE = "2.25"
      })
      trigger = { queue_arn = module.queues.queues["live-filings"].arn, batch_size = 10, max_concurrency = 2 }
    }
    kafka-to-store = {
      role_arn  = module.iam.lambda_role_arns["kafka-to-store"]
      directory = "/repo/sec-feature-pipeline/kafka-to-store"
      handler   = "handler.handle"
      memory    = 2048
      timeout   = 240
      environment = merge(local.sqs, local.lakehouse, {
        INPUT_QUEUE = local.queues["live-transactions"], OUTPUT_QUEUE = local.queues["live-tickers"], SYSTEM_INFERENCE = "true"
      })
      # One Iceberg commit per batch: wait up to 5 s to gather a filing burst
      trigger = { queue_arn = module.queues.queues["live-transactions"].arn, batch_size = 100, batching_window = 5, max_concurrency = 2 }
    }
    yahoo-connect = {
      role_arn    = module.iam.lambda_role_arns["yahoo-connect"]
      directory   = "/repo/prices-feature-pipeline/yahoo_connect"
      handler     = "src.handler.handle"
      memory      = 2048
      timeout     = 180
      environment = merge(local.lakehouse, local.inference_run, { BUFFER_SIZE = "50" })
    }
    target = {
      role_arn    = module.iam.lambda_role_arns.target
      directory   = "/repo/prices-feature-pipeline/target"
      handler     = "src.handler.handle"
      memory      = 2048
      timeout     = 180
      environment = merge(local.lakehouse, local.inference_run, { DELTA_PERIOD = "30" })
    }
    inference = {
      role_arn  = module.iam.lambda_role_arns.inference
      directory = "/repo/inference"
      handler   = "handler.handle"
      memory    = 2048
      timeout   = 180
      environment = merge(local.lakehouse, local.models, {
        ALPACA_BASE_URL = "https://paper-api.alpaca.markets", QTY = "1"
        SSM_PARAMETERS  = local.alpaca_secrets
      })
    }
  }
}

output "live" {
  value = module.live
}

module "dashboard" {
  source     = "../../modules/dashboard"
  name       = "secform4strategy"
  queues     = ["filings", "transactions", "live-filings", "live-transactions", "live-tickers"]
  eta_queues = ["filings", "transactions"]
  state_machine_arns = {
    retrain      = module.retrain.state_machine_arn
    "live-score" = module.live.score_state_machine_arn
  }
  function_names = [for service in keys(module.live.function_arns) : "secform4strategy-live-${service}"]
  log_groups = concat(
    values(module.tasks.log_groups),
    [for service in keys(module.live.function_arns) : "/aws/lambda/secform4strategy-live-${service}"],
  )
}

output "dashboard" {
  value = module.dashboard
}

module "site" {
  source         = "../../modules/site"
  site_directory = "${path.root}/../../../site"
  name           = "secform4strategy"
  # The publisher feeds the Live page, so it runs whenever the stack or the live path is on
  schedule_enabled             = var.switched_on || var.live_enabled
  publisher_role_name          = module.iam.lambda_role_names["site-publisher"]
  publisher_role_arn           = module.iam.lambda_role_arns["site-publisher"]
  live_score_state_machine_arn = module.live.score_state_machine_arn
  retrain_state_machine_arn    = module.retrain.state_machine_arn
  poller_schedule_name         = module.live.poller_schedule_name
  retrain_schedule_name        = module.retrain.schedule_name
  queue_arns                   = concat([for queue in module.queues.queues : queue.arn], values(module.queues.dead_letter_queues))
  publisher_environment = merge(local.lakehouse, local.models, {
    ALPACA_BASE_URL = "https://paper-api.alpaca.markets"
    SSM_PARAMETERS  = local.alpaca_secrets
  })
}

output "site" {
  value = module.site
}
