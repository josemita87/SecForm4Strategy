data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

locals {
  account = data.aws_caller_identity.current.account_id
  region  = data.aws_region.current.region
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

resource "aws_iam_role" "retrain" {
  name               = "${var.name}-retrain"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}

data "aws_iam_policy_document" "run_tasks" {
  statement {
    sid       = "RunTasks"
    actions   = ["ecs:RunTask"]
    resources = ["arn:aws:ecs:${local.region}:${local.account}:task-definition/${var.name}-*:*"]
  }
  statement {
    sid       = "FollowTasks"
    actions   = ["ecs:DescribeTasks", "ecs:StopTask"]
    resources = ["*"]
  }
  statement {
    sid       = "TaskEvents"
    actions   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
    resources = ["arn:aws:events:${local.region}:${local.account}:rule/StepFunctionsGetEventsForECSTaskRule"]
  }
  statement {
    sid       = "PassRoles"
    actions   = ["iam:PassRole"]
    resources = var.pass_role_arns
  }
}

resource "aws_iam_role_policy" "run_tasks" {
  name   = "run-tasks"
  role   = aws_iam_role.retrain.id
  policy = data.aws_iam_policy_document.run_tasks.json
}

locals {
  steps = ["master-index", "scraper", "kafka-to-store", "yahoo-connect", "target", "training"]
  network = {
    AwsvpcConfiguration = {
      Subnets        = var.subnet_ids
      SecurityGroups = [var.security_group_id]
      AssignPublicIp = "DISABLED"
    }
  }
}

resource "aws_sfn_state_machine" "retrain" {
  name     = "${var.name}-retrain"
  role_arn = aws_iam_role.retrain.arn
  definition = jsonencode({
    Comment       = "New filings to a new model, one ECS task per step"
    QueryLanguage = "JSONata"
    StartAt       = local.steps[0]
    States = {
      for index, step in local.steps : step => merge(
        {
          Type     = "Task"
          Resource = "arn:aws:states:::ecs:runTask.sync"
          Arguments = {
            Cluster              = var.cluster_arn
            TaskDefinition       = var.task_definition_arns[step]
            LaunchType           = "FARGATE"
            NetworkConfiguration = local.network
            Overrides = {
              ContainerOverrides = [{
                Name        = step
                Environment = "{% [$each($states.input.overrides.`${step}`, function($value, $name) { {'Name': $name, 'Value': $value} })] %}"
              }]
            }
          }
          Output = "{% $states.input %}"
          Retry = [{
            ErrorEquals     = ["States.TaskFailed"]
            IntervalSeconds = 600
            MaxAttempts     = 3
            BackoffRate     = 1
          }]
        },
        index < length(local.steps) - 1 ? { Next = local.steps[index + 1] } : { End = true }
      )
    }
  })
}

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${var.name}-retrain-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

data "aws_iam_policy_document" "start_retrain" {
  statement {
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.retrain.arn]
  }
}

resource "aws_iam_role_policy" "start_retrain" {
  name   = "start-retrain"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.start_retrain.json
}

resource "aws_scheduler_schedule" "weekly" {
  name                         = "${var.name}-weekly-retrain"
  schedule_expression          = "cron(0 6 ? * SAT *)"
  schedule_expression_timezone = "UTC"
  state                        = var.schedule_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.retrain.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({})

    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}
