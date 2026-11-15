param(
  [string]$Proyecto = "arquifusion-hackathon-514806",
  [string]$Region   = "us-central1",
  [string]$RegionModelo = "global",
  [string]$Servicio = "ia",
  [Parameter(Mandatory=$true)][string]$IaApiKey,
  [Parameter(Mandatory=$true)][string]$ApiAwsUrl,
  [Parameter(Mandatory=$true)][string]$ApiAwsKey,
  [string]$Modelo = "gemini-3.8-flash"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

gcloud config set project $Proyecto
gcloud services enable run.googleapis.com aiplatform.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

$vars = "GCP_PROJECT=$Proyecto,VERTEX_LOCATION=$RegionModelo,GEMINI_MODELO=$Modelo,MAX_TOKENS=4000,IA_API_KEY=$IaApiKey,API_AWS_URL=$ApiAwsUrl,API_AWS_KEY=$ApiAwsKey"

gcloud run deploy $Servicio `
  --source . `
  --region $Region `
  --platform managed `
  --allow-unauthenticated `
  --min-instances 0 `
  --max-instances 5 `
  --memory 512Mi `
  --timeout 120 `
  --set-env-vars $vars

$url = gcloud run services describe $Servicio --region $Region --format "value(status.url)"
Write-Output ""
Write-Output "URL: $url"
Write-Output "Health: $url/health"
