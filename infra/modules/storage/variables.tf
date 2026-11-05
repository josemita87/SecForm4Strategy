variable "name" {
  description = "Prefix for every resource name."
  type        = string
}

variable "services" {
  description = "Services that get an ECR repository"
  type        = set(string)
}

variable "images_kept" {
  description = "Images kept per repository; older ones expire"
  type        = number
  default     = 2
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget; alerts at 50% actual and 100% forecast."
  type        = number
}

variable "alert_email" {
  description = "Recipient of the budget alerts. "
  type        = string
}
