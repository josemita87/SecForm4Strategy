variable "name" {
  description = "Prefix for every resource name; images come from the ECR repositories <name>/<service>."
  type        = string
}

variable "functions" {
  description = "Per service: its role, its directory in the image, the handler, and the queue that triggers it, if any."
  type = map(object({
    role_arn    = string
    directory   = string
    handler     = string
    memory      = optional(number, 1024)
    timeout     = optional(number, 120)
    environment = optional(map(string), {})
    trigger = optional(object({
      queue_arn       = string
      batch_size      = optional(number, 10)
      batching_window = optional(number, 0)
      max_concurrency = optional(number, 2)
    }))
  }))
}

variable "poller" {
  description = "The function the schedule invokes every minute."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets the functions run in; they reach the internet through the NAT gateway."
  type        = list(string)
}

variable "security_group_id" {
  description = "Security group of the functions."
  type        = string
}

variable "schedule_enabled" {
  description = "Poll the filing feed every minute; off, the live path receives nothing."
  type        = bool
}

variable "score_steps" {
  description = "Functions the live-score state machine runs in order, each passing {\"tickers\": [...]} to the next."
  type        = list(string)
}

variable "tickers_queue_arn" {
  description = "Queue of the tickers whose filings were just stored; each batch starts one live-score run."
  type        = string
}
