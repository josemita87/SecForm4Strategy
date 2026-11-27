data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  # The plan role also plans each push to master, for the apply job to apply that exact plan
  subjects = {
    images = ["repo:${var.github_repository}:ref:refs/heads/master"]
    plan   = ["repo:${var.github_repository}:pull_request", "repo:${var.github_repository}:ref:refs/heads/master"]
    apply  = ["repo:${var.github_repository}:environment:aws"]
  }
}

data "aws_iam_policy_document" "github_assume" {
  for_each = local.subjects
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = each.value
    }
  }
}

resource "aws_iam_role" "github" {
  for_each           = local.subjects
  name               = "${var.name}-github-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.github_assume[each.key].json
}

data "aws_iam_policy_document" "push_images" {
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
    ]
    resources = ["arn:aws:ecr:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:repository/${var.name}/*"]
  }
}

resource "aws_iam_role_policy" "images" {
  name   = "push-images"
  role   = aws_iam_role.github["images"].id
  policy = data.aws_iam_policy_document.push_images.json
}

resource "aws_iam_role_policy_attachment" "plan" {
  role       = aws_iam_role.github["plan"].name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "state_lock" {
  statement {
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["arn:aws:s3:::${var.state_bucket}/*.tflock"]
  }
}

resource "aws_iam_role_policy" "plan" {
  name   = "state-lock"
  role   = aws_iam_role.github["plan"].id
  policy = data.aws_iam_policy_document.state_lock.json
}

# PR branches can assume the plan role: it reads neither the site password hash nor the SSM secrets (docs/aws.md#oidc-trust)
data "aws_iam_policy_document" "deny_secrets" {
  statement {
    effect    = "Deny"
    actions   = ["cloudfront-keyvaluestore:GetKey", "cloudfront-keyvaluestore:ListKeys"]
    resources = ["*"]
  }
  statement {
    effect    = "Deny"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParameterHistory"]
    resources = ["arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter/${var.name}/*"]
  }
  # Access to a path covers every parameter below it, so a recursive read of / would reach the secrets
  statement {
    effect    = "Deny"
    actions   = ["ssm:GetParametersByPath"]
    resources = ["*"]
  }
  statement {
    effect    = "Deny"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${data.aws_region.current.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "plan_deny_secrets" {
  name   = "deny-secrets"
  role   = aws_iam_role.github["plan"].id
  policy = data.aws_iam_policy_document.deny_secrets.json
}

resource "aws_iam_role_policy_attachment" "apply" {
  role       = aws_iam_role.github["apply"].name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
