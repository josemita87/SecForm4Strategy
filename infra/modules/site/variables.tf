variable "site_directory" {
  description = "Directory of the static site files; Terraform uploads every file except fixtures/, data/ and README.md."
  type        = string
}

variable "name" {
  description = "Prefix for every resource name; the publisher image comes from the ECR repository <name>/site-publisher."
  type        = string
}

variable "publisher_enabled" {
  description = "Create the publisher Lambda and its triggers; needs an image in <name>/site-publisher."
  type        = bool
}

variable "schedule_enabled" {
  description = "Publish every minute; off, only finished live-score runs publish."
  type        = bool
}

variable "publisher_role_name" {
  description = "Role the publisher runs with; this module adds the reads the site needs."
  type        = string
}

variable "publisher_role_arn" {
  description = "ARN of publisher_role_name."
  type        = string
}

variable "publisher_environment" {
  description = "Environment of the publisher beyond the site bucket, state machines and schedules this module sets."
  type        = map(string)
  default     = {}
}

variable "live_score_state_machine_arn" {
  description = "State machine whose finished runs trigger a publish and fill live.json."
  type        = string
}

variable "retrain_state_machine_arn" {
  description = "State machine whose latest run health.json reports."
  type        = string
}

variable "poller_schedule_name" {
  description = "Live poller schedule, reported as a switch."
  type        = string
}

variable "retrain_schedule_name" {
  description = "Weekly retrain schedule, reported as a switch."
  type        = string
}

variable "queue_arns" {
  description = "Queues and dead-letter queues whose depths health.json reports."
  type        = list(string)
}
