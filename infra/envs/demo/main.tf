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
    profile      = "secform4"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region              = "us-east-1"
  profile             = "secform4"
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
  source = "../../modules/queues"
  name   = "secform4strategy"
  queues = ["filings", "transactions"]
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
