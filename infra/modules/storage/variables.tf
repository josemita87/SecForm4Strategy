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
  default     = 5
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget; alerts at 50% actual and 100% forecast."
  type        = number
}

variable "alert_email" {
  description = "Recipient of the budget alerts. "
  type        = string
}

variable "vacuumed_tables" {
  description = "Iceberg tables of the Glue database that Athena VACUUMs every night: snapshot expiry and orphan file removal."
  type        = set(string)
  default     = []
}

variable "compacted_tables" {
  description = "Iceberg tables that Glue compacts; each must exist at apply. p_v1 also needs its 16 MiB write.target-file-size-bytes first (docs/aws.md#iceberg-maintenance)."
  type        = set(string)
  default     = []
}
