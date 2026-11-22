data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "read_secrets" {
  statement {
    actions   = ["ssm:GetParameters"]
    resources = ["arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter/${var.name}/*"]
  }
}

resource "aws_iam_role_policy" "read_secrets" {
  name   = "read-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.read_secrets.json
}

locals {
  bucket_arn = "arn:aws:s3:::${var.data_bucket}"
  glue_arns = [
    "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:catalog",
    "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:database/${var.glue_database}",
    "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:table/${var.glue_database}/*",
  ]
  glue_read = ["glue:GetDatabase", "glue:CreateDatabase", "glue:GetTable", "glue:GetTables"]

  capabilities = {
    lakehouse-read = [
      { actions = local.glue_read, resources = local.glue_arns },
      { actions = ["s3:GetObject"], resources = ["${local.bucket_arn}/warehouse/*"] },
      { actions = ["s3:ListBucket"], resources = [local.bucket_arn] },
    ]
    lakehouse-write = [
      { actions = concat(local.glue_read, ["glue:CreateTable", "glue:UpdateTable"]), resources = local.glue_arns },
      # DeleteObject: a commit that loses a race to a concurrent writer deletes the files it wrote
      { actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], resources = ["${local.bucket_arn}/warehouse/*"] },
      { actions = ["s3:ListBucket"], resources = [local.bucket_arn] },
    ]
    models-read = [
      { actions = ["s3:GetObject"], resources = ["${local.bucket_arn}/models/*"] },
      { actions = ["s3:ListBucket"], resources = [local.bucket_arn] },
    ]
    models-write = [
      { actions = ["s3:PutObject"], resources = ["${local.bucket_arn}/models/*"] },
    ]
    seen-keys = [
      { actions = ["dynamodb:BatchGetItem", "dynamodb:BatchWriteItem"], resources = ["arn:aws:dynamodb:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:table/${var.name}-seen"] },
    ]
    read-secrets = [
      { actions = ["ssm:GetParameters"], resources = ["arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter/${var.name}/*"] },
    ]
  }

  # Lambda keys get a live- prefix, so a service can have both a task role and a Lambda role
  access = merge(var.task_access, { for key, value in var.lambda_access : "live-${key}" => value })
}

data "aws_iam_policy_document" "access" {
  for_each = local.access

  dynamic "statement" {
    for_each = concat(
      [for queue in each.value.sends : { actions = ["sqs:GetQueueUrl", "sqs:SendMessage"], resources = [var.queue_arns[queue]] }],
      [for queue in each.value.receives : { actions = ["sqs:GetQueueUrl", "sqs:GetQueueAttributes", "sqs:ReceiveMessage", "sqs:DeleteMessage"], resources = [var.queue_arns[queue]] }],
      flatten([for capability in each.value.capabilities : local.capabilities[capability]]),
    )
    content {
      actions   = statement.value.actions
      resources = statement.value.resources
    }
  }
}

resource "aws_iam_role" "task" {
  for_each           = var.task_access
  name               = "${var.name}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy" "task" {
  for_each = var.task_access
  name     = "access"
  role     = aws_iam_role.task[each.key].id
  policy   = data.aws_iam_policy_document.access[each.key].json
}

resource "aws_iam_role" "lambda" {
  for_each           = var.lambda_access
  name               = "${var.name}-live-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "lambda" {
  for_each = var.lambda_access
  name     = "access"
  role     = aws_iam_role.lambda[each.key].id
  policy   = data.aws_iam_policy_document.access["live-${each.key}"].json
}

# Logs; the network-interface permissions let Lambda delete the VPC interfaces of earlier function versions
resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  for_each   = var.lambda_access
  role       = aws_iam_role.lambda[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}
