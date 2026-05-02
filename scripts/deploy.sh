#!/bin/bash
# Deploy a new version to ECS.
# Usage: bash scripts/deploy.sh [image-tag]
# Requires: aws CLI, docker, jq
set -euo pipefail

TAG="${1:-$(git rev-parse --short HEAD)}"
REGION="${AWS_REGION:-us-east-1}"

# Pull ECR URL from Terraform output
ECR_URL=$(terraform -chdir=infra output -raw ecr_repository_url)
CLUSTER=$(terraform -chdir=infra output -raw ecs_cluster_name)
SERVICE=$(terraform -chdir=infra output -raw ecs_cluster_name) # cluster == service name by default

echo "=== Building image $TAG ==="
docker build --platform linux/amd64 -t "$ECR_URL:$TAG" -t "$ECR_URL:latest" .

echo "=== Pushing to ECR ==="
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR_URL"
docker push "$ECR_URL:$TAG"
docker push "$ECR_URL:latest"

echo "=== Running migrations (one-off task) ==="
TASK_DEF=$(aws ecs describe-task-definition --task-definition attnd --query 'taskDefinition.taskDefinitionArn' --output text)
NETWORK=$(aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" \
  --query 'services[0].networkConfiguration' --output json)

TASK_ARN=$(aws ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition "$TASK_DEF" \
  --launch-type FARGATE \
  --network-configuration "$NETWORK" \
  --overrides '{"containerOverrides":[{"name":"attnd","command":["uv","run","python","manage.py","migrate","--noinput"]}]}' \
  --query 'tasks[0].taskArn' --output text)

echo "Waiting for migration task $TASK_ARN ..."
aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$TASK_ARN"

EXIT_CODE=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK_ARN" \
  --query 'tasks[0].containers[0].exitCode' --output text)

if [ "$EXIT_CODE" != "0" ]; then
  echo "ERROR: migrations exited with code $EXIT_CODE"
  exit 1
fi

echo "=== Deploying new image to ECS ==="
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --force-new-deployment > /dev/null
aws ecs wait services-stable --cluster "$CLUSTER" --services "$SERVICE"

echo "=== Done — $TAG is live ==="
