variable "name" {
  description = "Prefix for every queue name."
  type        = string
}

variable "queues" {
  description = "Queues to create, each with its own dead-letter queue."
  type        = set(string)
}

variable "visibility_timeout_seconds" {
  description = "How long a received message stays hidden before it is delivered again."
  type        = number
  default     = 300
}

variable "max_receives" {
  description = "Receives without an ack before a message moves to the dead-letter queue."
  type        = number
  default     = 5
}

variable "alert_email" {
  description = "Address the dead-letter alarms email."
  type        = string
  sensitive   = true
}
