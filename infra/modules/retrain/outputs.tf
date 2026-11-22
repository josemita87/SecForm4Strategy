output "state_machine_arn" {
  value = aws_sfn_state_machine.retrain.arn
}

output "schedule_name" {
  description = "The weekly retrain schedule."
  value       = aws_scheduler_schedule.weekly.name
}
