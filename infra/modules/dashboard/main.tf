data "aws_region" "current" {}

locals {
  region = data.aws_region.current.region
  # Metric rows: [namespace, metric, dimension, value, options]
  depth   = [for key in var.queues : ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", "${var.name}-${key}", { label = key }]]
  deletes = [for key in var.queues : ["AWS/SQS", "NumberOfMessagesDeleted", "QueueName", "${var.name}-${key}", { label = key }]]
  dead    = [for key in var.queues : ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", "${var.name}-${key}-dlq", { label = key }]]
  # ETA in hours: messages left / (deleted per period / seconds per period) / 3600; 0 while nothing is deleted
  # concat, not flatten: flatten would also flatten each metric row
  eta = concat([
    for index, key in var.eta_queues : [
      [{ expression = "IF(d${index} > 0, q${index} / (d${index} / PERIOD(d${index})) / 3600, 0)", label = "${key} ETA (hours)", id = "e${index}" }],
      ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", "${var.name}-${key}", { id = "q${index}", visible = false, stat = "Maximum" }],
      ["AWS/SQS", "NumberOfMessagesDeleted", "QueueName", "${var.name}-${key}", { id = "d${index}", visible = false, stat = "Sum" }],
    ]
  ]...)
  runs = concat([
    for label, arn in var.state_machine_arns : [
      for metric in ["ExecutionsStarted", "ExecutionsSucceeded", "ExecutionsFailed"] :
      ["AWS/States", metric, "StateMachineArn", arn, { label = "${label} ${trimprefix(metric, "Executions")}" }]
    ]
  ]...)
  lambda = { for metric in ["Invocations", "Errors", "Duration"] : metric => [
    for function in var.function_names : ["AWS/Lambda", metric, "FunctionName", function, { label = trimprefix(function, "${var.name}-live-") }]
  ] }
  sources = join(" | ", [for group in var.log_groups : "SOURCE '${group}'"])

  widgets = [
    { type = "text", x = 0, y = 0, width = 24, height = 1, properties = { markdown = "## ${var.name}: runs, queues, errors. `make status` prints the same in the terminal." } },
    { type = "metric", x = 0, y = 1, width = 8, height = 6, properties = { title = "Queue depth (messages waiting)", region = local.region, stat = "Maximum", period = 60, view = "timeSeries", metrics = local.depth } },
    { type = "metric", x = 8, y = 1, width = 8, height = 6, properties = { title = "Messages processed per minute", region = local.region, stat = "Sum", period = 60, view = "timeSeries", metrics = local.deletes } },
    { type = "metric", x = 16, y = 1, width = 8, height = 3, properties = { title = "Backlog ETA (hours, at the last 15 minutes' rate)", region = local.region, period = 900, view = "singleValue", metrics = local.eta } },
    { type = "metric", x = 16, y = 4, width = 8, height = 3, properties = { title = "Dead-letter queues (should be 0)", region = local.region, stat = "Maximum", period = 300, view = "singleValue", metrics = local.dead } },
    { type = "metric", x = 0, y = 7, width = 12, height = 6, properties = { title = "State machine runs", region = local.region, stat = "Sum", period = 3600, view = "timeSeries", metrics = local.runs } },
    { type = "metric", x = 12, y = 7, width = 12, height = 6, properties = { title = "Live Lambdas: errors", region = local.region, stat = "Sum", period = 300, view = "timeSeries", metrics = local.lambda.Errors } },
    { type = "metric", x = 0, y = 13, width = 12, height = 6, properties = { title = "Live Lambdas: invocations", region = local.region, stat = "Sum", period = 300, view = "timeSeries", metrics = local.lambda.Invocations } },
    { type = "metric", x = 12, y = 13, width = 12, height = 6, properties = { title = "Live Lambdas: duration p90 (ms, cold starts included)", region = local.region, stat = "p90", period = 300, view = "timeSeries", metrics = local.lambda.Duration } },
    { type = "log", x = 0, y = 19, width = 24, height = 6, properties = {
      title  = "ERROR log lines per service, per 15 minutes"
      region = local.region
      view   = "timeSeries"
      query  = "${local.sources} | filter @message like /ERROR/ | stats count(*) as errors by bin(15m), @log"
    } },
  ]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = var.name
  dashboard_body = jsonencode({ widgets = local.widgets })
}
