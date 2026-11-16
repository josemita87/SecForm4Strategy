variable "name" {
  description = "Prefix for every role name, and the SSM path holding the secrets."
  type        = string
}

variable "data_bucket" {
  description = "Bucket holding the Iceberg warehouse and the models."
  type        = string
}

variable "glue_database" {
  description = "Glue database holding the Iceberg tables."
  type        = string
}

variable "queue_arns" {
  description = "ARN per queue name."
  type        = map(string)
}

variable "task_access" {
  description = "Per service: queues it sends to and receives from, and its capabilities (lakehouse-read, lakehouse-write, models-read, models-write)."
  type = map(object({
    sends        = optional(list(string), [])
    receives     = optional(list(string), [])
    capabilities = optional(list(string), [])
  }))
}
