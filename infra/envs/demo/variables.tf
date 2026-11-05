variable "monthly_budget_usd" {
  description = "Monthly cost budget for the account."
  type        = number
  default     = 5
}

variable "alert_email" {
  description = "Recipient of the budget alerts; set in the git-ignored terraform.tfvars."
  type        = string
}
