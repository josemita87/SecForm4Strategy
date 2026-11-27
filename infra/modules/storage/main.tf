
data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "data" {
  bucket = "${var.name}-data-${data.aws_caller_identity.current.account_id}"
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "data_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.data.arn, "${aws_s3_bucket.data.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "data" {
  bucket     = aws_s3_bucket.data.id
  policy     = data.aws_iam_policy_document.data_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.data]
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
  # Versions that VACUUM, compaction or an overwrite replaced stay restorable for 7 days
  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 7 }
    expiration { expired_object_delete_marker = true }
  }
  rule {
    id     = "expire-athena-results"
    status = "Enabled"
    filter {
      prefix = "athena-results/"
    }
    expiration { days = 7 }
  }
  depends_on = [aws_s3_bucket_versioning.data]
}

resource "aws_glue_catalog_database" "lakehouse" {
  name         = var.name
  location_uri = "s3://${aws_s3_bucket.data.bucket}/warehouse"
}

resource "aws_athena_workgroup" "lakehouse" {
  name          = var.name
  force_destroy = true
  configuration {
    enforce_workgroup_configuration = true
    bytes_scanned_cutoff_per_query  = 1073741824
    result_configuration {
      output_location = "s3://${aws_s3_bucket.data.bucket}/athena-results/"
      encryption_configuration { encryption_option = "SSE_S3" }
    }
  }
}

resource "aws_athena_named_query" "demo" {
  for_each    = fileset("${path.module}/queries", "*.sql")
  name        = trimsuffix(each.value, ".sql")
  description = trimprefix(split("\n", file("${path.module}/queries/${each.value}"))[0], "-- ")
  workgroup   = aws_athena_workgroup.lakehouse.name
  database    = aws_glue_catalog_database.lakehouse.name
  query       = file("${path.module}/queries/${each.value}")
}

resource "aws_ecr_repository" "service" {
  for_each             = var.services
  name                 = "${var.name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "service" {
  for_each   = aws_ecr_repository.service
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the latest ${var.images_kept} images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = var.images_kept }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_budgets_budget" "monthly" {
  name         = "${var.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  cost_types {
    include_credit = false
    include_refund = false
  }
  dynamic "notification" {
    for_each = { ACTUAL = 50, FORECASTED = 100 }
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.key
      subscriber_email_addresses = [var.alert_email]

    }
  }
}

# Iceberg maintenance (docs/aws.md#iceberg-maintenance): Athena VACUUM on a schedule, Glue compaction
data "aws_region" "current" {}

locals {
  region_account = "${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}"
  glue_arns = [
    for resource in ["catalog", "database/${aws_glue_catalog_database.lakehouse.name}", "table/${aws_glue_catalog_database.lakehouse.name}/*"] :
    "arn:aws:glue:${local.region_account}:${resource}"
  ]
}

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

# Athena runs each VACUUM with this role's permissions: the table metadata, the warehouse files and the query results
resource "aws_iam_role" "vacuum" {
  name               = "${var.name}-vacuum"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "vacuum" {
  statement {
    actions   = ["athena:StartQueryExecution", "athena:GetQueryExecution"]
    resources = [aws_athena_workgroup.lakehouse.arn]
  }
  statement {
    actions   = ["glue:GetDatabase", "glue:GetTable", "glue:UpdateTable"]
    resources = local.glue_arns
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.data.arn}/warehouse/*", "${aws_s3_bucket.data.arn}/athena-results/*"]
  }
  statement {
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.data.arn]
  }
}

resource "aws_iam_role_policy" "vacuum" {
  name   = "vacuum"
  role   = aws_iam_role.vacuum.id
  policy = data.aws_iam_policy_document.vacuum.json
}

# 03:00 New York time is outside the live polls; a missing table only fails its own query
resource "aws_scheduler_schedule" "vacuum" {
  for_each                     = var.vacuumed_tables
  name                         = "${var.name}-vacuum-${replace(each.value, "_", "-")}"
  schedule_expression          = "cron(0 3 * * ? *)"
  schedule_expression_timezone = "America/New_York"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:athena:startQueryExecution"
    role_arn = aws_iam_role.vacuum.arn
    input = jsonencode({
      QueryString           = "VACUUM ${each.value}"
      QueryExecutionContext = { Database = aws_glue_catalog_database.lakehouse.name }
      WorkGroup             = aws_athena_workgroup.lakehouse.name
    })

    # The next night's VACUUM covers a failed one
    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}

data "aws_iam_policy_document" "glue_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "compaction" {
  name               = "${var.name}-compaction"
  assume_role_policy = data.aws_iam_policy_document.glue_assume.json
}

data "aws_iam_policy_document" "compaction" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.data.arn}/warehouse/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
  }
  statement {
    actions   = ["glue:GetTable", "glue:UpdateTable"]
    resources = local.glue_arns
  }
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${local.region_account}:log-group:/aws-glue/iceberg-compaction/logs:*"]
  }
}

resource "aws_iam_role_policy" "compaction" {
  name   = "compaction"
  role   = aws_iam_role.compaction.id
  policy = data.aws_iam_policy_document.compaction.json
}

# Glue compacts a table once it holds 500 files under 75% of write.target-file-size-bytes; each run bills its DPUs
resource "aws_glue_catalog_table_optimizer" "compaction" {
  for_each      = var.compacted_tables
  catalog_id    = data.aws_caller_identity.current.account_id
  database_name = aws_glue_catalog_database.lakehouse.name
  table_name    = each.value
  type          = "compaction"
  configuration {
    enabled  = true
    role_arn = aws_iam_role.compaction.arn
    compaction_configuration {
      iceberg_configuration {
        strategy        = "binpack"
        min_input_files = 500
      }
    }
  }
  depends_on = [aws_iam_role_policy.compaction]
}
