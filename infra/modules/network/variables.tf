variable "name" {
  description = "Prefix for every resource name"
  type        = string
}

variable "cidr_block" {
  description = "Address range of the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Availability zones with a public subnet"
  type        = number
  default     = 2
}

variable "nat_enabled" {
  description = "Create the NAT gateway, which gives the private subnets internet access"
  type        = bool
  default     = false
}
