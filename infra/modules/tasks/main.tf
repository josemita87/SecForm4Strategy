
resource "aws_ecs_cluster" "main" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
}

resource "aws_cloudwatch_log_group" "service" {
  for_each          = var.tasks
  name              = "/${var.name}/${each.key}"
  retention_in_days = 14
}

data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

data "aws_ecr_image" "latest" {
  for_each        = var.tasks
  repository_name = "${var.name}/${each.key}"
  most_recent     = true
}

resource "aws_ecs_task_definition" "service" {
  for_each                 = var.tasks
  family                   = "${var.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = each.value.task_role_arn
  # A new revision leaves the old one active: running Step Functions executions name exact revisions
  skip_destroy = true
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }
  container_definitions = jsonencode([{
    name        = each.key
    image       = data.aws_ecr_image.latest[each.key].image_uri
    essential   = true
    environment = [for key, value in each.value.environment : { name = key, value = value }]
    secrets = [
      for key, parameter in each.value.secrets : {
        name      = key
        valueFrom = "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter/${var.name}/${parameter}"
      }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.service[each.key].name
        awslogs-region        = data.aws_region.current.region
        awslogs-stream-prefix = "task"
      }
    }
  }])
}
