#!/usr/bin/env bash
# Builds and pushes the service images from this machine, as the images workflow does in CI.
# Tags match CI (the last commit that changed the service or the shared clients), so CI later skips them.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
export AWS_PROFILE="${AWS_PROFILE:-secform4}"
region=us-east-1

docker info >/dev/null 2>&1 || { echo 'Start Docker Desktop first' >&2; exit 1; }

registry="$(aws sts get-caller-identity --query Account --output text).dkr.ecr.${region}.amazonaws.com"
aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "$registry" >/dev/null

services=(
  master-index:sec-feature-pipeline/master-index
  scraper:sec-feature-pipeline/scraper
  kafka-to-store:sec-feature-pipeline/kafka-to-store
  yahoo-connect:prices-feature-pipeline/yahoo_connect
  target:prices-feature-pipeline/target
  training:training
  inference:inference
)

for entry in "${services[@]}"; do
  service=${entry%%:*} path=${entry#*:}
  repository="secform4strategy/${service}"
  tag=$(git log -1 --format=%h --abbrev=12 -- "$path" packages/secform4strategy-clients)

  # The tag names a commit, so the build context must hold exactly that commit's sources
  if [[ -n $(git status --porcelain -- "$path" packages/secform4strategy-clients) ]]; then
    echo "Commit or stash the changes under ${path} or the clients package first" >&2
    exit 1
  fi

  # Tags are immutable, so an image that already exists is never rebuilt
  if [[ $(aws ecr batch-get-image --region "$region" --repository-name "$repository" \
      --image-ids "imageTag=${tag}" --query 'length(images)' --output text) != 0 ]]; then
    echo "${service}:${tag} already pushed"
    continue
  fi

  echo "Building ${service}:${tag}"
  docker buildx build --platform linux/arm64 --provenance=false --push --build-arg "GIT_SHA=${tag}" \
    --file "${path}/Dockerfile" --tag "${registry}/${repository}:${tag}" .

  size=$(aws ecr describe-images --region "$region" --repository-name "$repository" \
    --image-ids "imageTag=${tag}" --query 'imageDetails[0].imageSizeInBytes' --output text)
  echo "${service}:${tag} pushed, $((size / 1048576)) MB compressed"
done
