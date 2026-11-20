output "function_arns" {
  description = "ARN per live-path function."
  value       = { for service, function in aws_lambda_function.service : service => function.arn }
}

output "seen_table" {
  description = "DynamoDB table of the filings the poller has sent."
  value       = aws_dynamodb_table.seen.name
}

output "score_state_machine_arn" {
  description = "State machine scoring and trading the tickers of new filings."
  value       = aws_sfn_state_machine.score.arn
}
