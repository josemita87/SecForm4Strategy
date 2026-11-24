output "queues" {
  description = "Name and ARN per queue."
  value = {
    for key, queue in aws_sqs_queue.main : key => { name = queue.name, arn = queue.arn }
  }
}

output "dead_letter_queues" {
  description = "ARN of each queue's dead-letter queue."
  value       = { for key, queue in aws_sqs_queue.dead_letter : key => queue.arn }
}

output "alerts_topic_arn" {
  description = "SNS topic that emails the operator; other modules' alarms publish to it too."
  value       = aws_sns_topic.alerts.arn
}
