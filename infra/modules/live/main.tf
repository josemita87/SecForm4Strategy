# Keys the poller has already sent; DynamoDB deletes each one after expires_at
resource "aws_dynamodb_table" "seen" {
  name         = "${var.name}-seen"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "key"

  attribute {
    name = "key"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

resource "aws_cloudwatch_log_group" "function" {
  for_each          = var.functions
  name              = "/aws/lambda/${var.name}-live-${each.key}"
  retention_in_days = 14
}

data "aws_ecr_image" "latest" {
  for_each        = var.functions
  repository_name = "${var.name}/${each.key}"
  most_recent     = true
}

# The batch task's image; awslambdaric runs the handler instead of main.py (docs/aws.md)
resource "aws_lambda_function" "service" {
  for_each      = var.functions
  function_name = "${var.name}-live-${each.key}"
  role          = each.value.role_arn
  package_type  = "Image"
  image_uri     = data.aws_ecr_image.latest[each.key].image_uri
  architectures = ["arm64"]
  memory_size   = each.value.memory
  timeout       = each.value.timeout

  reserved_concurrent_executions = each.value.reserved_concurrency

  image_config {
    entry_point = ["${each.value.directory}/.venv/bin/python", "-m", "awslambdaric"]
    command     = [each.value.handler]
    # Flat services import from src/, package services from their directory
    working_directory = startswith(each.value.handler, "src.") ? each.value.directory : "${each.value.directory}/src"
  }

  environment {
    # The image's file system is read-only; libraries that cache under HOME write to /tmp
    variables = merge({ HOME = "/tmp" }, each.value.environment)
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.function[each.key].name
  }
}

resource "aws_lambda_event_source_mapping" "queue" {
  for_each                           = { for key, function in var.functions : key => function.trigger if function.trigger != null }
  event_source_arn                   = each.value.queue_arn
  function_name                      = aws_lambda_function.service[each.key].arn
  batch_size                         = each.value.batch_size
  maximum_batching_window_in_seconds = each.value.batching_window
  # The handler returns the messages it did not finish; only they are delivered again
  function_response_types = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = each.value.max_concurrency
  }
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name}-live-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "invoke_poller" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.service[var.poller].arn]
  }
}

resource "aws_iam_role_policy" "invoke_poller" {
  name   = "invoke-poller"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.invoke_poller.json
}

resource "aws_scheduler_schedule" "poll" {
  name                = "${var.name}-live-poll"
  schedule_expression = "rate(1 minute)"
  state               = var.schedule_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.service[var.poller].arn
    role_arn = aws_iam_role.scheduler.arn

    # The next poll a minute later covers a missed one
    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}

locals {
  # SQS bodies reach the state machine as JSON text: one run scores every ticker of the batch
  batch_tickers = "{% {'tickers': $distinct($states.input.($type(body) = 'string' ? $parse(body) : body).tickers)} %}"
  score_states = {
    for index, step in var.score_steps : step => merge(
      {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Arguments = {
          FunctionName = aws_lambda_function.service[step].arn
          Payload      = index == 0 ? local.batch_tickers : "{% $states.input %}"
        }
        Output = "{% $states.result.Payload %}"
        # Lambda's transient errors, and handlers raising RetryableError (e.g. orders left unplaced)
        # A single-writer step is throttled while another run uses it: wait for it, up to about 10 minutes
        Retry = [
          { ErrorEquals = ["Lambda.TooManyRequestsException"], IntervalSeconds = 5, MaxAttempts = 25, BackoffRate = 1.5, MaxDelaySeconds = 30, JitterStrategy = "FULL" },
          { ErrorEquals = ["States.TaskFailed"], IntervalSeconds = 5, MaxAttempts = 2, BackoffRate = 2 },
        ]
      },
      index == length(var.score_steps) - 1 ? { End = true } : { Next = var.score_steps[index + 1] },
    )
  }
}

data "aws_iam_policy_document" "states_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "score" {
  name               = "${var.name}-live-score"
  assume_role_policy = data.aws_iam_policy_document.states_assume.json
}

data "aws_iam_policy_document" "invoke_steps" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [for step in var.score_steps : aws_lambda_function.service[step].arn]
  }
}

resource "aws_iam_role_policy" "invoke_steps" {
  name   = "invoke-steps"
  role   = aws_iam_role.score.id
  policy = data.aws_iam_policy_document.invoke_steps.json
}

resource "aws_sfn_state_machine" "score" {
  name     = "${var.name}-live-score"
  role_arn = aws_iam_role.score.arn
  definition = jsonencode({
    Comment       = "Prices, rows and trades for the tickers of new filings"
    QueryLanguage = "JSONata"
    StartAt       = var.score_steps[0]
    States        = local.score_states
  })
}

data "aws_iam_policy_document" "pipes_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["pipes.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "pipe" {
  name               = "${var.name}-live-tickers-pipe"
  assume_role_policy = data.aws_iam_policy_document.pipes_assume.json
}

data "aws_iam_policy_document" "pipe" {
  statement {
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [var.tickers_queue_arn]
  }
  statement {
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.score.arn]
  }
}

resource "aws_iam_role_policy" "pipe" {
  name   = "tickers-to-score"
  role   = aws_iam_role.pipe.id
  policy = data.aws_iam_policy_document.pipe.json
}

# Starts one live-score run per batch of ticker messages, without waiting for it
resource "aws_pipes_pipe" "tickers" {
  name     = "${var.name}-live-tickers"
  role_arn = aws_iam_role.pipe.arn
  source   = var.tickers_queue_arn
  target   = aws_sfn_state_machine.score.arn

  source_parameters {
    sqs_queue_parameters {
      batch_size = 10
    }
  }

  target_parameters {
    step_function_state_machine_parameters {
      invocation_type = "FIRE_AND_FORGET"
    }
  }
}
