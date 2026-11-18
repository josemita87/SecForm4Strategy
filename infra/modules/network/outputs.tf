output "vpc_id" {
  description = "The VPC the tasks run in."
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "Private subnets the tasks run in, one per availability zone."
  value       = aws_subnet.private[*].id
}

output "security_group_id" {
  description = "Security group of the tasks: outbound only."
  value       = aws_security_group.tasks.id
}
