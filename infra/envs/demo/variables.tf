variable "monthly_budget_usd" {
  description = "Monthly cost budget for the account."
  type        = number
  default     = 5
}

variable "alert_email" {
  description = "Recipient of the budget alerts; terraform.tfvars locally, the ALERT_EMAIL secret in CI."
  type        = string
  sensitive   = true
}

variable "switched_on" {
  description = "Switch the stack on: creates the NAT gateway and, later, enables the schedules."
  type        = bool
  default     = false
}

variable "weekly_retrain_enabled" {
  description = "Run the Saturday retrain while switched on; off while a long backfill uses the same queues."
  type        = bool
  default     = true
}

variable "live_enabled" {
  description = "Poll for new filings every minute while switched on; off while a full-rate backfill uses SEC's limit."
  type        = bool
  default     = true
}

variable "site_publisher_enabled" {
  description = "Create the site publisher Lambda and its triggers; true only once CI has pushed its image."
  type        = bool
  default     = false
}
