#!/usr/bin/env bash
# Store the hash of the site's Basic-auth header in the CloudFront key-value store; run it in your own terminal
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-secform4}"
read -rp 'Site user: ' user
read -rsp 'Site password: ' password && echo
hash=$(printf 'Basic %s' "$(printf '%s:%s' "$user" "$password" | base64 | tr -d '\n')" | shasum -a 256 | cut -d' ' -f1)
arn=$(aws cloudfront describe-key-value-store --name secform4strategy-site-auth --query 'KeyValueStore.ARN' --output text)
etag=$(aws cloudfront-keyvaluestore describe-key-value-store --kvs-arn "$arn" --query ETag --output text)
aws cloudfront-keyvaluestore put-key --kvs-arn "$arn" --key credential_sha256 --value "$hash" --if-match "$etag" >/dev/null
echo 'Password set; CloudFront applies it within about a minute.'
