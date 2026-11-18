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
      { actions = ["s3:GetObject", "s3:PutObject"], resources = ["${local.bucket_arn}/warehouse/*"] },
      { actions = ["s3:ListBucket"], resources = [local.bucket_arn] },
    ]
    models-read = [
      { actions = ["s3:GetObject"], resources = ["${local.bucket_arn}/models/*"] },
      { actions = ["s3:ListBucket"], resources = [local.bucket_arn] },
    ]
    models-write = [
      { actions = ["s3:PutObject"], resources = ["${local.bucket_arn}/models/*"] },
    ]
  }
}

data "aws_iam_policy_document" "task" {
  for_each = var.task_access

  dynamic "statement" {
    for_each = concat(
      [for queue in each.value.sends : { actions = ["sqs:GetQueueUrl", "sqs:SendMessage"], resources = [var.queue_arns[queue]] }],
      [for queue in each.value.receives : { actions = ["sqs:GetQueueUrl", "sqs:ReceiveMessage", "sqs:DeleteMessage"], resources = [var.queue_arns[queue]] }],
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
  policy   = data.aws_iam_policy_document.task[each.key].json
}
