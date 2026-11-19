variable "name" {
  description = "Prefix for every resource name."
  type        = string
}

variable "cluster_arn" {
  description = "ECS cluster the tasks run on."
  type        = string
}

variable "task_definition_arns" {
  description = "Task definition per service."
  type        = map(string)
}

variable "pass_role_arns" {
  description = "Execution and task roles the state machine hands to ECS."
  type        = list(string)
}

variable "subnet_ids" {
  description = "Private subnets the tasks run in."
  type        = list(string)
}

variable "security_group_id" {
  description = "Security group of the tasks."
  type        = string
}

variable "schedule_enabled" {
  description = "Whether the weekly retrain runs; true only while the stack is switched on."
  type        = bool
}
