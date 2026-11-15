param(
    [string]$Carpeta = ""
)

$ErrorActionPreference = "Stop"

$project = (& gcloud config get-value project 2>$null).Trim()
if ([string]::IsNullOrWhiteSpace($project) -or $project -eq "(unset)") {
    throw "No hay proyecto configurado. Ejecuta: gcloud config set project <PROJECT_ID>"
}

$dataset = "arquifusion"
$location = "US"
$schemaDir = Join-Path $PSScriptRoot "esquemas"

if ([string]::IsNullOrWhiteSpace($Carpeta)) {
    $Carpeta = Join-Path $PSScriptRoot "..\..\simulador\salida"
}
if (-not (Test-Path -LiteralPath $Carpeta -PathType Container)) {
    throw "No existe la carpeta de CSV: $Carpeta"
}
$Carpeta = (Resolve-Path -LiteralPath $Carpeta).Path

$cargas = @(
    [pscustomobject]@{ Tabla = "telemetria"; Csv = "telemetria.csv"; Esquema = "telemetria.json" },
    [pscustomobject]@{ Tabla = "fallas"; Csv = "fallas.csv"; Esquema = "fallas.json" },
    [pscustomobject]@{ Tabla = "incidentes"; Csv = "incidentes.csv"; Esquema = "incidentes.json" },
    [pscustomobject]@{ Tabla = "mantenimientos"; Csv = "mantenimientos.csv"; Esquema = "mantenimientos.json" },
    [pscustomobject]@{ Tabla = "consumo_repuestos"; Csv = "consumo_repuestos.csv"; Esquema = "consumo_repuestos.json" }
)

function Leer-CabeceraCsv {
    param([string]$Ruta)

    $linea = Get-Content -LiteralPath $Ruta -TotalCount 1 -Encoding UTF8
    if ($null -eq $linea) {
        throw "CSV vacio: $Ruta"
    }
    $linea = $linea.TrimStart([char]0xFEFF)
    return @($linea.Split(","))
}

Write-Host "Proyecto: $project"
Write-Host "Carpeta de CSV: $Carpeta"
$cargadas = @()

foreach ($item in $cargas) {
    $csvPath = Join-Path $Carpeta $item.Csv
    if (-not (Test-Path -LiteralPath $csvPath -PathType Leaf)) {
        Write-Host "Omitido: no existe $csvPath"
        continue
    }

    $schemaPath = Join-Path $schemaDir $item.Esquema
    if (-not (Test-Path -LiteralPath $schemaPath -PathType Leaf)) {
        throw "No existe el esquema: $schemaPath"
    }

    $csvNames = Leer-CabeceraCsv -Ruta $csvPath
    $schema = Get-Content -LiteralPath $schemaPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $schemaNames = @($schema | ForEach-Object { $_.name })

    if (($csvNames -join "|") -ne ($schemaNames -join "|")) {
        throw "Cabecera de '$($item.Csv)' no coincide con '$($item.Esquema)'. CSV: $($csvNames -join ',') | Esquema: $($schemaNames -join ',')"
    }

    & bq --project_id $project show "$dataset.$($item.Tabla)" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "No existe la tabla $dataset.$($item.Tabla). Ejecuta primero crear_tablas.ps1."
    }

    Write-Host "Cargando $($item.Csv) -> $dataset.$($item.Tabla) (--replace)..."
    & bq --project_id $project load --location=$location --source_format=CSV --skip_leading_rows=1 --replace "$dataset.$($item.Tabla)" $csvPath $schemaPath
    if ($LASTEXITCODE -ne 0) {
        throw "Fallo bq load para $($item.Tabla)."
    }

    if ($item.Tabla -eq "telemetria") {
        $alterSql = @'
ALTER TABLE `arquifusion.telemetria` ADD COLUMN IF NOT EXISTS ingest_ts TIMESTAMP;
ALTER TABLE `arquifusion.telemetria` ALTER COLUMN ingest_ts SET DEFAULT CURRENT_TIMESTAMP();
UPDATE `arquifusion.telemetria` SET ingest_ts = CURRENT_TIMESTAMP() WHERE TRUE;
'@
        $alterSql | & bq --project_id $project query --nouse_legacy_sql --location=$location
        if ($LASTEXITCODE -ne 0) {
            throw "No se pudo restaurar ingest_ts en telemetria."
        }
    }

    $cargadas += $item.Tabla
}

if ($cargadas.Count -eq 0) {
    throw "No se encontro ningun CSV para cargar en $Carpeta."
}

$selects = @()
foreach ($tabla in $cargadas) {
    $selects += "SELECT '$tabla' AS tabla, COUNT(*) AS filas FROM $dataset.$tabla"
}
$countSql = ($selects -join " UNION ALL ") + " ORDER BY tabla"

Write-Host "Conteo de filas:"
& bq --project_id $project query --nouse_legacy_sql --location=$location $countSql
if ($LASTEXITCODE -ne 0) {
    throw "Fallo el conteo de filas."
}

