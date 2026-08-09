#!/usr/bin/env bash
# Build the linux/amd64 image, push to ECR, roll the ECS service.
#
# Usage: AWS_PROFILE=<profile> AWS_REGION=<region> ./scripts/deploy_aws.sh
set -euo pipefail
cd "$(dirname "$0")/.."

REPO=$(terraform -chdir=infra output -raw ecr_repository_url)
CLUSTER=$(terraform -chdir=infra output -raw ecs_cluster)
SERVICE=$(terraform -chdir=infra output -raw ecs_service)
REGISTRY=${REPO%%/*}

aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY"

docker build --platform linux/amd64 -t "$REPO:latest" .
docker push "$REPO:latest"

aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
  --force-new-deployment --query "service.deployments[0].id" --output text

echo "deployment rolling; watch: aws ecs describe-services --cluster $CLUSTER --services $SERVICE"
