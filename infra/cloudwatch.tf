# ── Log group ─────────────────────────────────────────────────────────────────
# ECS ships all container stdout here via the awslogs driver.
# Django logs to stdout in JSON format so CloudWatch Insights can parse fields.
resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.prefix}"
  retention_in_days = var.environment == "prod" ? 90 : 14
}

# ── SNS topic for alerts ──────────────────────────────────────────────────────
resource "aws_sns_topic" "alerts" {
  name = "${local.prefix}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ── Metric filters ────────────────────────────────────────────────────────────
# These scan the JSON log stream for specific fields. CloudWatch Insights can
# also query the raw JSON directly: `fields @timestamp, levelname, message`.

resource "aws_cloudwatch_log_metric_filter" "errors" {
  name           = "${local.prefix}-errors"
  log_group_name = aws_cloudwatch_log_group.app.name
  # Matches both plain-text [ERROR] and JSON {"levelname":"ERROR"} lines
  pattern = "?\"levelname\":\"ERROR\" ?\"[ERROR]\""

  metric_transformation {
    name          = "ErrorCount"
    namespace     = "Attnd/${var.environment}"
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_log_metric_filter" "warnings" {
  name           = "${local.prefix}-warnings"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "?\"levelname\":\"WARNING\" ?\"[WARNING]\""

  metric_transformation {
    name          = "WarningCount"
    namespace     = "Attnd/${var.environment}"
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_log_metric_filter" "http_5xx" {
  name           = "${local.prefix}-5xx"
  log_group_name = aws_cloudwatch_log_group.app.name
  # gunicorn logs: "GET /path HTTP/1.1" 5XX
  pattern = "[..., status_code=5*, ...]"

  metric_transformation {
    name          = "5xxCount"
    namespace     = "Attnd/${var.environment}"
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

# ── Alarms ────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "error_rate" {
  alarm_name          = "${local.prefix}-error-rate"
  alarm_description   = "Application ERROR log rate is elevated"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "ErrorCount"
  namespace           = "Attnd/${var.environment}"
  period              = 300  # 5 minutes
  statistic           = "Sum"
  threshold           = var.environment == "prod" ? 5 : 20
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${local.prefix}-alb-5xx"
  alarm_description   = "ALB is returning 5xx errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = var.environment == "prod" ? 10 : 50
  treat_missing_data  = "notBreaching"
  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
  }
  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "ecs_cpu" {
  alarm_name          = "${local.prefix}-ecs-cpu"
  alarm_description   = "ECS task CPU consistently above 80%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/ECS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  treat_missing_data  = "notBreaching"
  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.app.name
  }
  alarm_actions = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${local.prefix}-rds-cpu"
  alarm_description   = "RDS CPU above 80%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  treat_missing_data  = "notBreaching"
  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }
  alarm_actions = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "rds_storage" {
  alarm_name          = "${local.prefix}-rds-storage"
  alarm_description   = "RDS free storage below 5 GB"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "FreeStorageSpace"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 5368709120  # 5 GB in bytes
  treat_missing_data  = "notBreaching"
  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }
  alarm_actions = [aws_sns_topic.alerts.arn]
}

# ── Dashboard ─────────────────────────────────────────────────────────────────
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.prefix

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "log"
        x = 0; y = 0; width = 24; height = 6
        properties = {
          title   = "Recent Errors"
          region  = var.aws_region
          view    = "table"
          query   = "SOURCE '/ecs/${local.prefix}' | fields @timestamp, levelname, name, message | filter levelname = 'ERROR' | sort @timestamp desc | limit 50"
        }
      },
      {
        type   = "metric"
        x = 0; y = 6; width = 8; height = 6
        properties = {
          title  = "Error / Warning Count (5 min)"
          region = var.aws_region
          metrics = [
            ["Attnd/${var.environment}", "ErrorCount"],
            ["Attnd/${var.environment}", "WarningCount"],
          ]
          period = 300
          stat   = "Sum"
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x = 8; y = 6; width = 8; height = 6
        properties = {
          title  = "ALB Request Count & 5xx"
          region = var.aws_region
          metrics = [
            ["AWS/ApplicationELB", "RequestCount",             "LoadBalancer", aws_lb.main.arn_suffix],
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count","LoadBalancer", aws_lb.main.arn_suffix],
          ]
          period = 300
          stat   = "Sum"
          view   = "timeSeries"
        }
      },
      {
        type   = "metric"
        x = 16; y = 6; width = 8; height = 6
        properties = {
          title  = "ECS CPU & Memory"
          region = var.aws_region
          metrics = [
            ["AWS/ECS", "CPUUtilization",    "ClusterName", aws_ecs_cluster.main.name, "ServiceName", aws_ecs_service.app.name],
            ["AWS/ECS", "MemoryUtilization", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", aws_ecs_service.app.name],
          ]
          period = 300
          stat   = "Average"
          view   = "timeSeries"
        }
      }
    ]
  })
}
