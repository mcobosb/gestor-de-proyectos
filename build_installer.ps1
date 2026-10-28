$ErrorActionPreference = "Stop"

$repositoryRoot = $PSScriptRoot
Set-Location $repositoryRoot

$pythonCommand = Get-Command python -ErrorAction Stop
$python = $pythonCommand.Source
& $python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Se requiere Python 3.11 o posterior."
}
& $python -c "import sys; sys.exit(0 if sys.maxsize > 2**32 else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "La compilación del instalador requiere Python de 64 bits."
}

& $python -c "import nuitka"
if ($LASTEXITCODE -ne 0) {
    throw "Falta Nuitka. Instala las dependencias con: python -m pip install -r requirements.txt -r requirements-build.txt"
}

$innoCompiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if (-not $innoCompiler) {
    $defaultCompiler = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
    if (Test-Path $defaultCompiler) {
        $innoCompilerPath = $defaultCompiler
    }
} else {
    $innoCompilerPath = $innoCompiler.Source
}
if (-not $innoCompilerPath) {
    throw "No se encuentra ISCC.exe. Instala Inno Setup 6 o añade ISCC.exe al PATH."
}

$nuitkaOutput = Join-Path $repositoryRoot "build\nuitka"
$distributionDirectory = Join-Path $nuitkaOutput "GestorDeProyectos.dist"

& $python -m nuitka `
    --standalone `
    --mingw64 `
    --assume-yes-for-downloads `
    --enable-plugin=tk-inter `
    --windows-console-mode=disable `
    --windows-icon-from-ico=iconos\logo.ico `
    --company-name="Manuel Cobos Budia" `
    --product-name="Gestor de Proyectos" `
    --file-version=1.0.0 `
    --product-version=1.0.0 `
    --file-description="Gestión de presupuestos, planificación y certificaciones" `
    --output-dir=$nuitkaOutput `
    --output-filename=GestorDeProyectos.exe `
    --include-data-dir=iconos=iconos `
    --include-data-dir=plantillas=plantillas `
    --include-package-data=matplotlib `
    bc3_gui.py
if ($LASTEXITCODE -ne 0) {
    throw "La compilación Nuitka ha fallado."
}
if (-not (Test-Path (Join-Path $distributionDirectory "GestorDeProyectos.exe"))) {
    throw "Nuitka terminó sin generar el ejecutable esperado en $distributionDirectory."
}
if (Test-Path (Join-Path $distributionDirectory "bc3_gui.py")) {
    throw "La carpeta de distribución contiene código fuente sin compilar."
}

& $innoCompilerPath installer\GestorDeProyectos.iss
if ($LASTEXITCODE -ne 0) {
    throw "La creación del instalador con Inno Setup ha fallado."
}

Write-Host "Instalador creado en dist\GestorDeProyectos-Setup-1.0.0.exe"
