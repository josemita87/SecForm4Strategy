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
