
variable "name" {
  description = "Prefix for every resource name."
  type        = string
}

variable "execution_role_arn" {
  description = "Role ECS uses to pull images, write logs and read secrets."
  type        = string
}

variable "tasks" {
  description = "Settings per service; secrets map a variable name to an SSM parameter under /<name>/."
  type = map(object({
    task_role_arn = string
    cpu           = optional(number, 512)
    memory        = optional(number, 1024)
    environment   = optional(map(string), {})
    secrets       = optional(map(string), {})
  }))
}
