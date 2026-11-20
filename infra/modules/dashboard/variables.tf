variable "name" {
  description = "Prefix of every resource the dashboard shows, and the dashboard's name."
  type        = string
}

variable "queues" {
  description = "Queue keys; each has the queue <name>-<key> and the dead-letter queue <name>-<key>-dlq."
  type        = list(string)
}

variable "eta_queues" {
  description = "Queue keys whose backlog gets an ETA: depth divided by the rate of deletes."
  type        = list(string)
}

variable "state_machine_arns" {
  description = "State machine ARN per label."
  type        = map(string)
}

variable "function_names" {
  description = "Lambda function names."
  type        = list(string)
}

variable "log_groups" {
  description = "Log groups whose ERROR lines are counted."
  type        = list(string)
}
