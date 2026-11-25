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
    # 1 makes the function a single writer: overlapping live-score runs queue for it instead of writing the same table at once
    reserved_concurrency = optional(number)
    trigger = optional(object({
      queue_arn       = string
      batch_size      = optional(number, 10)
      batching_window = optional(number, 0)
      max_concurrency = optional(number, 2)
    }))
  }))
}

variable "poller" {
  description = "The function the schedules invoke."
  type        = string
}

variable "poll_schedule" {
  description = "Scheduler cron expressions in New York time: `every` polls the feed's newest page, `catch_up` reads `catch_up_pages` pages once, before `every` starts."
  type = object({
    every          = string
    catch_up       = string
    catch_up_pages = number
  })
}

variable "schedule_enabled" {
  description = "Run both poll schedules; off, the live path receives nothing."
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
