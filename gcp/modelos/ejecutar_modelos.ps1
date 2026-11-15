param(
    [int]$Desde = 0,
    [int]$Solo = 0
)

$ErrorActionPreference = "Stop"
$env:PYTHONWARNINGS = "ignore"

$project = (& gcloud config get-value project 2>$null).Trim()
if ([string]::IsNullOrWhiteSpace($project) -or $project -eq "(unset)") {
    throw "No hay proyecto configurado. Ejecuta: gcloud config set project <PROJECT_ID>"
}

$location = "US"
$sqlDir = $PSScriptRoot

$pasos = @(
    [pscustomobject]@{ Numero = 1; Archivo = "01_vistas_falla.sql" },
    [pscustomobject]@{ Numero = 2; Archivo = "02_modelo_logit.sql" },
    [pscustomobject]@{ Numero = 3; Archivo = "03_modelo_boosted.sql" },
    [pscustomobject]@{ Numero = 4; Archivo = "04_metricas.sql" },
    [pscustomobject]@{ Numero = 5; Archivo = "05_pesos_export.sql" },
    [pscustomobject]@{ Numero = 6; Archivo = "06_validar_falla.sql" },
    [pscustomobject]@{ Numero = 7; Archivo = "07_arima_horas.sql" },
    [pscustomobject]@{ Numero = 8; Archivo = "08_arima_repuestos.sql" },
    [pscustomobject]@{ Numero = 9; Archivo = "09_arima_combustible.sql" },
    [pscustomobject]@{ Numero = 10; Archivo = "10_pronosticos.sql" },
    [pscustomobject]@{ Numero = 11; Archivo = "11_flota.sql" }
)

if ($Desde -gt 0) {
    $pasos = @($pasos | Where-Object { $_.Numero -ge $Desde })
}
if ($Solo -gt 0) {
    $pasos = @($pasos | Where-Object { $_.Numero -eq $Solo })
}
if ($pasos.Count -eq 0) {
    throw "No hay pasos para ejecutar con Desde=$Desde y Solo=$Solo."
}

Write-Host "Proyecto: $project"
foreach ($paso in $pasos) {
    $ruta = Join-Path $sqlDir $paso.Archivo
    if (-not (Test-Path -LiteralPath $ruta -PathType Leaf)) {
        throw "No existe el archivo: $ruta"
    }

    Write-Host "Paso $($paso.Numero): $($paso.Archivo)"
    $inicio = Get-Date
    $sql = Get-Content -LiteralPath $ruta -Raw -Encoding UTF8
    $sql | & bq --project_id $project query --nouse_legacy_sql --location=$location
    if ($LASTEXITCODE -ne 0) {
        throw "Fallo el paso $($paso.Numero): $($paso.Archivo)"
    }

    $segundos = [math]::Round(((Get-Date) - $inicio).TotalSeconds, 1)
    Write-Host "OK $($paso.Archivo) en $segundos s"
}

Write-Host "Pipeline de modelos completado."


