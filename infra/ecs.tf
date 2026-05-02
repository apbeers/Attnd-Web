resource "aws_ecs_cluster" "main" {
  name = local.prefix
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# Log group defined in cloudwatch.tf — referenced here by name

resource "aws_ecs_task_definition" "app" {
  family                   = local.prefix
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.app_cpu
  memory                   = var.app_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name  = var.app_name
    image = "${aws_ecr_repository.app.repository_url}:latest"

    portMappings = [{
      containerPort = var.app_port
      protocol      = "tcp"
    }]

    environment = [
      { name = "PORT",          value = tostring(var.app_port) },
      { name = "DEBUG",         value = "0" },
      { name = "ALLOWED_HOSTS", value = ".${var.domain_name},${aws_lb.main.dns_name}" },
      { name = "AWS_REGION",    value = var.aws_region },
    ]

    # All app secrets injected from Secrets Manager at task startup.
    # Bedrock access is via the task IAM role — no API key secret needed.
    secrets = [
      { name = "DATABASE_URL",           valueFrom = "${aws_secretsmanager_secret.app.arn}:DATABASE_URL::" },
      { name = "SECRET_KEY",             valueFrom = "${aws_secretsmanager_secret.app.arn}:SECRET_KEY::" },
      { name = "APPLE_APP_BUNDLE_ID",    valueFrom = "${aws_secretsmanager_secret.app.arn}:APPLE_APP_BUNDLE_ID::" },
      { name = "APPLE_TEAM_ID",          valueFrom = "${aws_secretsmanager_secret.app.arn}:APPLE_TEAM_ID::" },
      { name = "ANDROID_PACKAGE_NAME",   valueFrom = "${aws_secretsmanager_secret.app.arn}:ANDROID_PACKAGE_NAME::" },
      { name = "ATTESTATION_JWT_SECRET", valueFrom = "${aws_secretsmanager_secret.app.arn}:ATTESTATION_JWT_SECRET::" },
      { name = "ATTND_API_KEY",          valueFrom = "${aws_secretsmanager_secret.app.arn}:ATTND_API_KEY::" },
      { name = "AWS_STORAGE_BUCKET_NAME", valueFrom = "${aws_secretsmanager_secret.app.arn}:AWS_STORAGE_BUCKET_NAME::" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = "/ecs/${local.prefix}"
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "ecs"
      }
    }
  }])
}

resource "aws_ecs_service" "app" {
  name            = local.prefix
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  launch_type     = "FARGATE"
  desired_count   = var.app_desired_count
  force_new_deployment = true

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = var.app_name
    container_port   = var.app_port
  }

  depends_on = [aws_lb_listener.https]

  lifecycle {
    ignore_changes = [desired_count, task_definition]
  }
}
