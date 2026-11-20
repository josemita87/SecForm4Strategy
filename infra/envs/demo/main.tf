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
    "yahoo-connect", "target", "training", "inference"
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
  queues      = ["filings", "transactions"]
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
    training       = { capabilities = ["lakehouse-read", "models-write"] }
    inference      = { capabilities = ["lakehouse-read", "models-read"] }
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
      memory        = 2048
      environment = merge(local.sqs, local.lakehouse, {
        INPUT_QUEUE = local.queues.transactions, BUFFER_SIZE = "100", POLL_TIMEOUT = "20", SYSTEM_TRAINING = "true"
      })
    }
    yahoo-connect = {
      task_role_arn = local.roles["yahoo-connect"]
      memory        = 2048
      environment   = merge(local.lakehouse, local.training_run, { BUFFER_SIZE = "50" })
    }
    target = {
      task_role_arn = local.roles.target
      cpu           = 1024
      memory        = 4096
      environment   = merge(local.lakehouse, local.training_run, { DELTA_PERIOD = "30" })
    }
    training = {
      task_role_arn = local.roles.training
      cpu           = 1024
      memory        = 4096
      environment = merge(local.lakehouse, local.models, {
        TRAIN_TEST_RATIO = "0.2", RANDOM_STATE = "42", TARGET_FEATURE = "pct_change"
      })
    }
    inference = {
      task_role_arn = local.roles.inference
      memory        = 2048
      environment = merge(local.lakehouse, local.models, {
        ALPACA_BASE_URL = "https://paper-api.alpaca.markets", QTY = "1", PREDICTION_THRESHOLD = "-0.05"
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
}

output "retrain" {
  value = module.retrain
}
