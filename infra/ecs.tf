# ── Cluster ───────────────────────────────────────────────────────────────────
resource "aws_ecs_cluster" "main" {
  name = var.app_name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# ── CloudWatch log group ──────────────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.app_name}"
  retention_in_days = 30
}

# ── Task definition ───────────────────────────────────────────────────────────
locals {
  # DATABASE_URL assembled from RDS outputs; secrets injected from Secrets Manager
  db_url = "postgresql://${var.db_username}:${urlencode(aws_secretsmanager_secret_version.db_password.secret_string)}@${aws_db_instance.main.address}:5432/${var.db_name}"
}

resource "aws_ecs_task_definition" "app" {
  family                   = var.app_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.app_cpu
  memory                   = var.app_memory

  execution_role_arn = aws_iam_role.ecs_execution.arn
  task_role_arn      = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name  = var.app_name
    image = "${aws_ecr_repository.app.repository_url}:latest"

    portMappings = [{
      containerPort = var.app_port
      protocol      = "tcp"
    }]

    environment = [
      { name = "PORT",           value = tostring(var.app_port) },
      { name = "DEBUG",          value = "0" },
      { name = "ALLOWED_HOSTS",  value = ".${var.domain_name},${aws_lb.main.dns_name}" },
      { name = "DATABASE_URL",   value = local.db_url },
      { name = "AWS_STORAGE_BUCKET_NAME", value = aws_s3_bucket.media.bucket },
      { name = "AWS_REGION",     value = var.aws_region },
    ]

    # Individual keys pulled from the app JSON secret
    secrets = [
      { name = "SECRET_KEY",             valueFrom = "${aws_secretsmanager_secret.app.arn}:SECRET_KEY::" },
      { name = "APPLE_APP_BUNDLE_ID",    valueFrom = "${aws_secretsmanager_secret.app.arn}:APPLE_APP_BUNDLE_ID::" },
      { name = "APPLE_TEAM_ID",          valueFrom = "${aws_secretsmanager_secret.app.arn}:APPLE_TEAM_ID::" },
      { name = "ANDROID_PACKAGE_NAME",   valueFrom = "${aws_secretsmanager_secret.app.arn}:ANDROID_PACKAGE_NAME::" },
      { name = "ATTESTATION_JWT_SECRET", valueFrom = "${aws_secretsmanager_secret.app.arn}:ATTESTATION_JWT_SECRET::" },
      { name = "ATTND_API_KEY",          valueFrom = "${aws_secretsmanager_secret.app.arn}:ATTND_API_KEY::" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "ecs"
      }
    }

    # Mirrors the Heroku release phase: migrate + collectstatic on every deploy
    # Run this as a one-off task before updating the service (see deploy script).
    # Keeping it here as documentation of what needs to run.
  }])
}

# ── Service ───────────────────────────────────────────────────────────────────
resource "aws_ecs_service" "app" {
  name            = var.app_name
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  launch_type     = "FARGATE"
  desired_count   = var.app_desired_count

  # Allow Terraform deploys to update the task definition without forcing replacement
  force_new_deployment = true

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true # needed since there's no NAT Gateway
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = var.app_name
    container_port   = var.app_port
  }

  depends_on = [aws_lb_listener.https]

  lifecycle {
    # Prevent Terraform from resetting desired_count if you've manually scaled
    ignore_changes = [desired_count]
  }
}
