variable "name" {
  description = "Prefix for every role name, and of the ECR repositories."
  type        = string
}

variable "github_repository" {
  description = "Repository whose workflows may assume the roles, as owner/name."
  type        = string
}

variable "state_bucket" {
  description = "Bucket holding the Terraform state."
  type        = string
}
