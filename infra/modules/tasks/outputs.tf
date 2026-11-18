
output "cluster_arn" {
  value = aws_ecs_cluster.main.arn
}

output "log_groups" {
  value = { for service, group in aws_cloudwatch_log_group.service : service => group.name }
}

output "task_definition_arns" {
  value = { for service, task in aws_ecs_task_definition.service : service => task.arn }
}
