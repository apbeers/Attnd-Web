#!/bin/bash
# Run once locally to create the Terraform state backend and GitHub Actions IAM roles.
# Requires: AWS CLI configured with admin credentials, Terraform >= 1.7
#
# After running, copy the outputs into GitHub secrets:
#   AWS_DEV_ROLE_ARN  → value of dev_deploy_role_arn
#   AWS_PROD_ROLE_ARN → value of prod_deploy_role_arn
#
# That's the only manual step. Everything else runs automatically in CI.

set -euo pipefail
cd "$(dirname "$0")/bootstrap"

echo "=== Initializing bootstrap (local state) ==="
terraform init -input=false

echo "=== Planning ==="
terraform plan -input=false -out=bootstrap.tfplan

echo "=== Applying ==="
terraform apply -input=false bootstrap.tfplan

echo ""
echo "=== Bootstrap complete ==="
echo "Add these to GitHub repository secrets:"
echo ""
terraform output -json | python3 -c "
import json, sys
o = json.load(sys.stdin)
print(f'  AWS_DEV_ROLE_ARN  = {o[\"dev_deploy_role_arn\"][\"value\"]}')
print(f'  AWS_PROD_ROLE_ARN = {o[\"prod_deploy_role_arn\"][\"value\"]}')
"
