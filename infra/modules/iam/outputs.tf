output "execution_role_arn" {
  description = "Role the ECS agent uses to pull images, write logs and read secrets."
  value       = aws_iam_role.execution.arn
}

output "task_role_arns" {
  description = "Role each service's code runs with."
  value       = { for service, role in aws_iam_role.task : service => role.arn }
}

output "lambda_role_arns" {
  description = "Role each live-path Lambda runs with."
  value       = { for service, role in aws_iam_role.lambda : service => role.arn }
}
