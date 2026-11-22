data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  region  = data.aws_region.current.region
  account = data.aws_caller_identity.current.account_id
  # Execution ARNs of a state machine: arn:aws:states:<region>:<account>:execution:<state machine>:<run>
  execution_arns = [for arn in [var.live_score_state_machine_arn, var.retrain_state_machine_arn] : "${replace(arn, ":stateMachine:", ":execution:")}:*"]
}

resource "aws_s3_bucket" "site" {
  bucket = "${var.name}-site-${local.account}"
  # CI and the publisher rebuild every object
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Only this distribution reads the bucket; ListBucket makes a missing file a 404 instead of a 403
data "aws_iam_policy_document" "site_bucket" {
  statement {
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = ["${aws_s3_bucket.site.arn}/*", aws_s3_bucket.site.arn]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site_bucket.json
  # A bucket policy naming a service principal is not public, but the block must exist first
  depends_on = [aws_s3_bucket_public_access_block.site]
}

locals {
  # Every site file except the local preview fixtures and the README; data/ belongs to the publisher
  site_files = toset([for file in fileset(var.site_directory, "**") : file if !startswith(file, "fixtures/") && !startswith(file, "data/") && file != "README.md"])
  content_types = {
    html = "text/html; charset=utf-8"
    css  = "text/css; charset=utf-8"
    js   = "text/javascript; charset=utf-8"
    json = "application/json"
    svg  = "image/svg+xml"
    png  = "image/png"
    ico  = "image/x-icon"
  }
}

resource "aws_s3_object" "site" {
  for_each     = local.site_files
  bucket       = aws_s3_bucket.site.id
  key          = each.value
  source       = "${var.site_directory}/${each.value}"
  etag         = filemd5("${var.site_directory}/${each.value}")
  content_type = lookup(local.content_types, reverse(split(".", each.value))[0], "application/octet-stream")
}

# Holds the SHA-256 of the expected authorization header; written by hand (scripts/set_site_password.sh), never by Terraform
resource "aws_cloudfront_key_value_store" "auth" {
  name    = "${var.name}-site-auth"
  comment = "Site password hash"
}

resource "aws_cloudfront_function" "auth" {
  name                         = "${var.name}-site-auth"
  runtime                      = "cloudfront-js-2.0"
  comment                      = "HTTP Basic auth for the site"
  publish                      = true
  key_value_store_associations = [aws_cloudfront_key_value_store.auth.arn]
  code                         = file("${path.module}/auth.js")
}

resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${var.name}-site"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# HSTS, nosniff, frame-ancestors same origin and a strict referrer policy on every response
data "aws_cloudfront_response_headers_policy" "security" {
  name = "Managed-SecurityHeadersPolicy"
}

# Maximum TTL in seconds per cache behaviour, compressed (Managed-CachingDisabled does not compress)
resource "aws_cloudfront_cache_policy" "site" {
  for_each = {
    static = 60 # Static files change only at an apply, which does not invalidate the cache
    data   = 5  # The publisher rewrites data/ every minute
  }
  name        = "${var.name}-site-${each.key}"
  min_ttl     = 0
  default_ttl = each.value
  max_ttl     = each.value

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_gzip   = true
    enable_accept_encoding_brotli = true
    cookies_config {
      cookie_behavior = "none"
    }
    headers_config {
      header_behavior = "none"
    }
    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

resource "aws_cloudfront_distribution" "site" {
  enabled             = true
  comment             = "${var.name} site"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  http_version        = "http2and3"
  is_ipv6_enabled     = true

  origin {
    origin_id                = "site"
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  default_cache_behavior {
    target_origin_id           = "site"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = aws_cloudfront_cache_policy.site["static"].id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security.id
    compress                   = true

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.auth.arn
    }
  }

  ordered_cache_behavior {
    path_pattern               = "data/*"
    target_origin_id           = "site"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = aws_cloudfront_cache_policy.site["data"].id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security.id
    compress                   = true

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.auth.arn
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

data "aws_iam_policy_document" "publisher" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.site.arn}/data/*"]
  }
  # ListBucket turns a missing data file into NoSuchKey instead of AccessDenied (docs/site.md)
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.site.arn]
  }
  statement {
    actions   = ["states:ListExecutions"]
    resources = [var.live_score_state_machine_arn, var.retrain_state_machine_arn]
  }
  statement {
    actions   = ["states:DescribeExecution", "states:GetExecutionHistory"]
    resources = local.execution_arns
  }
  statement {
    actions   = ["sqs:GetQueueAttributes"]
    resources = var.queue_arns
  }
  # ListQueues takes no resource; the publisher finds the project queues by name prefix
  statement {
    actions   = ["sqs:ListQueues"]
    resources = ["*"]
  }
  statement {
    actions   = ["scheduler:GetSchedule"]
    resources = [for schedule in [var.poller_schedule_name, var.retrain_schedule_name] : "arn:aws:scheduler:${local.region}:${local.account}:schedule/default/${schedule}"]
  }
  # GetMetricData supports no resource-level permissions
  statement {
    actions   = ["cloudwatch:GetMetricData"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "publisher" {
  name   = "site"
  role   = var.publisher_role_name
  policy = data.aws_iam_policy_document.publisher.json
}

resource "aws_cloudwatch_log_group" "publisher" {
  name              = "/aws/lambda/${var.name}-site-publisher"
  retention_in_days = 14
}

data "aws_ecr_image" "publisher" {
  repository_name = "${var.name}/site-publisher"
  most_recent     = true
}

# The account's concurrency limit is 10, the minimum unreserved, so no function can reserve concurrency
resource "aws_lambda_function" "publisher" {
  function_name = "${var.name}-site-publisher"
  role          = var.publisher_role_arn
  package_type  = "Image"
  image_uri     = data.aws_ecr_image.publisher.image_uri
  architectures = ["arm64"]
  memory_size   = 1536
  timeout       = 300

  image_config {
    entry_point       = ["/repo/site-publisher/.venv/bin/python", "-m", "awslambdaric"]
    command           = ["handler.handle"]
    working_directory = "/repo/site-publisher/src"
  }

  environment {
    variables = merge(var.publisher_environment, {
      HOME                         = "/tmp"
      NAME_PREFIX                  = var.name
      SITE_BUCKET                  = aws_s3_bucket.site.bucket
      LIVE_SCORE_STATE_MACHINE_ARN = var.live_score_state_machine_arn
      RETRAIN_STATE_MACHINE_ARN    = var.retrain_state_machine_arn
      POLLER_SCHEDULE_NAME         = var.poller_schedule_name
      RETRAIN_SCHEDULE_NAME        = var.retrain_schedule_name
    })
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.publisher.name
  }
}

# Both triggers invoke asynchronously: drop an event a minute old instead of queueing publishes behind a slow one
resource "aws_lambda_function_event_invoke_config" "publisher" {
  function_name                = aws_lambda_function.publisher.function_name
  maximum_event_age_in_seconds = 60
  maximum_retry_attempts       = 0
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
  name               = "${var.name}-site-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

data "aws_iam_policy_document" "invoke_publisher" {
  statement {
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.publisher.arn]
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "invoke-publisher"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.invoke_publisher.json
}

resource "aws_scheduler_schedule" "publish" {
  name                = "${var.name}-site-publish"
  schedule_expression = "rate(1 minute)"
  state               = var.schedule_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.publisher.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ mode = "all" })

    # The next publish a minute later covers a missed one
    retry_policy {
      maximum_retry_attempts = 0
    }
  }
}

resource "aws_cloudwatch_event_rule" "live_score_finished" {
  name        = "${var.name}-site-live-score-finished"
  description = "Publishes live.json when a live-score run ends"
  event_pattern = jsonencode({
    source        = ["aws.states"]
    "detail-type" = ["Step Functions Execution Status Change"]
    detail = {
      stateMachineArn = [var.live_score_state_machine_arn]
      status          = ["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"]
    }
  })
}

resource "aws_cloudwatch_event_target" "live_score_finished" {
  rule  = aws_cloudwatch_event_rule.live_score_finished.name
  arn   = aws_lambda_function.publisher.arn
  input = jsonencode({ mode = "live" })
}

resource "aws_lambda_permission" "live_score_finished" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.publisher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.live_score_finished.arn
}

# The publisher resources had count = 1 while the removed site_publisher_enabled switch was on
moved {
  from = aws_cloudwatch_log_group.publisher[0]
  to   = aws_cloudwatch_log_group.publisher
}

moved {
  from = aws_lambda_function.publisher[0]
  to   = aws_lambda_function.publisher
}

moved {
  from = aws_lambda_function_event_invoke_config.publisher[0]
  to   = aws_lambda_function_event_invoke_config.publisher
}

moved {
  from = aws_iam_role.scheduler[0]
  to   = aws_iam_role.scheduler
}

moved {
  from = aws_iam_role_policy.scheduler[0]
  to   = aws_iam_role_policy.scheduler
}

moved {
  from = aws_scheduler_schedule.publish[0]
  to   = aws_scheduler_schedule.publish
}

moved {
  from = aws_cloudwatch_event_rule.live_score_finished[0]
  to   = aws_cloudwatch_event_rule.live_score_finished
}

moved {
  from = aws_cloudwatch_event_target.live_score_finished[0]
  to   = aws_cloudwatch_event_target.live_score_finished
}

moved {
  from = aws_lambda_permission.live_score_finished[0]
  to   = aws_lambda_permission.live_score_finished
}
