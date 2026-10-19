#!/usr/bin/env bash
# Deploy the job scout as a Cloud Run Job triggered by Cloud Scheduler (Mon + Thu 7:00 AM Eastern).
# Run from the repo root in Google Cloud Shell (https://shell.cloud.google.com) or any machine with gcloud.
#
#   export PROJECT_ID=your-gcp-project
#   ./scripts/deploy.sh
#
# First time only, create the secrets (see README):
#   printf '%s' "YOUR-GEMINI-KEY" | gcloud secrets create gemini-api-key --data-file=-
#   gcloud secrets create gmail-oauth --data-file=gmail-oauth.json      # optional (Gmail drafts + digest)
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-east1}"
JOB="${JOB:-coop-job-scout}"
BUCKET="${BUCKET:-${PROJECT_ID}-coop-scout}"
SA_NAME="coop-scout-runner"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULE="${SCHEDULE:-0 7 * * 1,4}"

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com storage.googleapis.com gmail.googleapis.com

# Storage for the "already reported" list and the digests
gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1 || gcloud storage buckets create "gs://${BUCKET}" --location="$REGION"

# Least-privilege identity the job runs as
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || gcloud iam service-accounts create "$SA_NAME" --display-name="Co-op job scout"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" --member="serviceAccount:${SA}" --role=roles/storage.objectAdmin
gcloud secrets add-iam-policy-binding gemini-api-key --member="serviceAccount:${SA}" --role=roles/secretmanager.secretAccessor
SECRETS="GEMINI_API_KEY=gemini-api-key:latest"
if gcloud secrets describe gmail-oauth >/dev/null 2>&1; then
  gcloud secrets add-iam-policy-binding gmail-oauth --member="serviceAccount:${SA}" --role=roles/secretmanager.secretAccessor
  SECRETS="${SECRETS},GMAIL_OAUTH_JSON=gmail-oauth:latest"
else
  echo "NOTE: no gmail-oauth secret - the job will write digests to the bucket only (no Gmail drafts)."
fi

# Your private profile (copy of config/profile.yaml) lives in Secret Manager, never in the image:
#   gcloud secrets create coop-profile --data-file=config/profile.yaml
gcloud secrets add-iam-policy-binding coop-profile --member="serviceAccount:${SA}" --role=roles/secretmanager.secretAccessor
SECRETS="${SECRETS},PROFILE_YAML=coop-profile:latest"

# Build from source and create/update the job
gcloud run jobs deploy "$JOB" --source . --region "$REGION" --service-account "$SA" \
  --set-secrets "$SECRETS" \
  --set-env-vars "SEEN_PATH=gs://${BUCKET}/seen.json,DIGEST_DIR=gs://${BUCKET}/digests,GEMINI_MODEL=${GEMINI_MODEL:-gemini-2.5-flash}" \
  --task-timeout 30m --max-retries 0 --memory 1Gi

# Let Cloud Scheduler start the job
gcloud run jobs add-iam-policy-binding "$JOB" --region "$REGION" --member="serviceAccount:${SA}" --role=roles/run.invoker
URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB}:run"
if gcloud scheduler jobs describe "${JOB}-schedule" --location "$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "${JOB}-schedule" --location "$REGION" --schedule "$SCHEDULE" --time-zone "America/New_York" \
    --uri "$URI" --http-method POST --oauth-service-account-email "$SA"
else
  gcloud scheduler jobs create http "${JOB}-schedule" --location "$REGION" --schedule "$SCHEDULE" --time-zone "America/New_York" \
    --uri "$URI" --http-method POST --oauth-service-account-email "$SA"
fi

echo
echo "Deployed. Run it now:   gcloud run jobs execute ${JOB} --region ${REGION} --wait"
echo "Read the digests:       gcloud storage cat gs://${BUCKET}/digests/digest-\$(date +%F).md"
