# ECS Cluster
resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# CloudWatch Log Groups
resource "aws_cloudwatch_log_group" "services" {
  for_each          = toset(["event-service", "ticket-service", "payment-service", "booking-orchestrator"])
  name              = "/ecs/${var.project_name}/${each.key}"
  retention_in_days = 7
}

# IAM Role for ECS Tasks
resource "aws_iam_role" "ecs_task_execution" {
  name = "${var.project_name}-ecs-task-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Action    = "sts:AssumeRole",
      Effect    = "Allow",
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ECR Repositories
resource "aws_ecr_repository" "services" {
  for_each             = toset(["event-service", "ticket-service", "payment-service", "booking-orchestrator"])
  name                 = "${var.project_name}/${each.key}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

# ECS Task Definitions
locals {
  services = {
    "event-service" = {
      port = 8001
      environment = [
        { name = "DATABASE_URL", value = "postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.postgres.endpoint}/events_db" }
      ]
    },
    "ticket-service" = {
      port = 8002
      environment = [
        { name = "DATABASE_URL", value = "postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.postgres.endpoint}/tickets_db" },
        { name = "REDIS_URL", value = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379/0" }
      ]
    },
    "payment-service" = {
      port        = 8003
      environment = []
    },
    "booking-orchestrator" = {
      port = 8004
      environment = [
        { name = "TICKET_SERVICE_URL", value = "http://ticket-service.${var.project_name}.local:8002" },
        { name = "PAYMENT_SERVICE_URL", value = "http://payment-service.${var.project_name}.local:8003" }
      ]
    }
  }
}

resource "aws_ecs_task_definition" "services" {
  for_each = local.services

  family                   = "${var.project_name}-${each.key}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([{
    name  = each.key,
    image = "${aws_ecr_repository.services[each.key].repository_url}:${var.ecr_image_tag}",
    portMappings = [{
      containerPort = each.value.port,
      protocol      = "tcp"
    }],
    environment = each.value.environment,
    logConfiguration = {
      logDriver = "awslogs",
      options = {
        "awslogs-group"         = "/ecs/${var.project_name}/${each.key}",
        "awslogs-region"        = var.aws_region,
        "awslogs-stream-prefix" = "ecs"
      }
    },
    healthCheck = {
      command     = ["CMD-SHELL", "curl -f http://localhost:${each.value.port}/health || exit 1"],
      interval    = 30,
      timeout     = 5,
      retries     = 3,
      startPeriod = 60
    }
  }])
}

resource "aws_ecs_service" "services" {
  for_each = local.services

  name            = each.key
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.services[each.key].arn
  desired_count   = 2
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }
}

# Application Load Balancer
resource "aws_lb" "main" {
  name               = "${var.project_name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Ticketing System API"
      status_code  = "200"
    }
  }
}
