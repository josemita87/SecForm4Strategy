variable "monthly_budget_usd" {
  description = "Monthly cost budget for the account."
  type        = number
  default     = 10
}

variable "alert_email" {
  description = "Recipient of the budget alerts; terraform.tfvars locally, the ALERT_EMAIL secret in CI."
  type        = string
  sensitive   = true
}

variable "switched_on" {
  description = "Switch the batch path on: creates the NAT gateway and lets the weekly retrain run."
  type        = bool
  default     = false
}

variable "weekly_retrain_enabled" {
  description = "Run the Saturday retrain while switched on; off while a long backfill uses the same queues."
  type        = bool
  default     = true
}

variable "live_enabled" {
  description = "Poll for new filings every minute, independently of switched_on (the live chain needs no NAT gateway); off while a full-rate backfill uses SEC's limit."
  type        = bool
  default     = false
}
