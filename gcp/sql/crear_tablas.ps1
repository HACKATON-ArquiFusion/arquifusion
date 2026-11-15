$ErrorActionPreference = "Stop"

$project = (& gcloud config get-value project 2>$null).Trim()
if ([string]::IsNullOrWhiteSpace($project) -or $project -eq "(unset)") {
    throw "No hay proyecto configurado. Ejecuta: gcloud config set project <PROJECT_ID>"
}

$dataset = "arquifusion"
$location = "US"
$sqlPath = Join-Path $PSScriptRoot "00_tablas.sql"
$datasetRef = "${project}:$dataset"

if (-not (Test-Path -LiteralPath $sqlPath)) {
    throw "No existe el archivo SQL: $sqlPath"
}

Write-Host "Proyecto: $project"
Write-Host "Dataset: $dataset | Ubicacion: $location"

& bq show --dataset $datasetRef 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creando dataset $dataset..."
    & bq --project_id $project mk --dataset --location=$location --description "Datos ArquiFusion" $dataset
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo crear el dataset $dataset."
    }
} else {
    Write-Host "El dataset $dataset ya existe."
}

$sql = Get-Content -LiteralPath $sqlPath -Raw
Write-Host "Ejecutando 00_tablas.sql..."
& bq --project_id $project query --nouse_legacy_sql --location=$location $sql
if ($LASTEXITCODE -ne 0) {
    throw "Fallo la ejecucion de 00_tablas.sql."
}

Write-Host "Tablas creadas o verificadas correctamente."
