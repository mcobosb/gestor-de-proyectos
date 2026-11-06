$ErrorActionPreference = "Stop"

$repositoryRoot = $PSScriptRoot
Set-Location $repositoryRoot
$env:MPLBACKEND = "Agg"

$virtualEnvironmentPython = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
if (Test-Path $virtualEnvironmentPython) {
    $python = $virtualEnvironmentPython
} else {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $python = $pythonCommand.Source
}
& $python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Se requiere Python 3.11 o posterior. Crea el entorno con: py -3.11 -m venv .venv"
}
& $python -c "import sys; sys.exit(0 if sys.maxsize > 2**32 else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "La compilación del instalador requiere Python de 64 bits."
}
$compilerOptions = @("--msvc=latest")
& $python -c "import sys; sys.exit(0 if sys.version_info < (3, 13) else 1)"
if ($LASTEXITCODE -eq 0) {
    $compilerOptions = @("--mingw64")
}

& $python -c "import nuitka"
if ($LASTEXITCODE -ne 0) {
    throw "Falta Nuitka. Instala las dependencias con: python -m pip install -r requirements.txt -r requirements-build.txt"
}
& $python -c "import pdfminer, pdfplumber, pypdfium2"
if ($LASTEXITCODE -ne 0) {
    throw "Faltan dependencias de ejecución. Instálalas con: python -m pip install -r requirements.txt"
}

$innoCompiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($innoCompiler) {
    $innoCompilerPath = $innoCompiler.Source
} else {
    foreach ($innoVersion in 7, 6) {
        foreach ($programFilesDirectory in @(${env:ProgramFiles}, ${env:ProgramFiles(x86)})) {
            if (-not $programFilesDirectory) {
                continue
            }

            $defaultCompiler = Join-Path $programFilesDirectory "Inno Setup $innoVersion\ISCC.exe"
            if (Test-Path $defaultCompiler) {
                $innoCompilerPath = $defaultCompiler
                break
            }
        }

        if ($innoCompilerPath) {
            break
        }
    }
}
if (-not $innoCompilerPath) {
    throw "No se encuentra ISCC.exe. Instala Inno Setup 7 o 6, o añade ISCC.exe al PATH."
}

$nuitkaOutput = Join-Path $repositoryRoot "build\nuitka"
$distributionDirectory = Join-Path $nuitkaOutput "bc3_gui.dist"
$toolsOutput = Join-Path $nuitkaOutput "tools"
New-Item -ItemType Directory -Force -Path $toolsOutput | Out-Null
$compiledTools = @()
foreach ($tool in Get-ChildItem (Join-Path $repositoryRoot "tools") -Filter "*.py") {
    if ($tool.Name -eq "__init__.py") {
        continue
    }
    $moduleOutput = Join-Path $toolsOutput $tool.BaseName
    if (Test-Path $moduleOutput) {
        Get-ChildItem -LiteralPath $moduleOutput -Filter "$($tool.BaseName)*.pyd" -File |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
    }
    & $python -m nuitka `
        --module `
        --nofollow-imports `
        @compilerOptions `
        --assume-yes-for-downloads `
        --output-dir=$moduleOutput `
        $tool.FullName
    if ($LASTEXITCODE -ne 0) {
        throw "La compilación de la herramienta $($tool.Name) ha fallado."
    }
    $extension = @(Get-ChildItem $moduleOutput -Filter "$($tool.BaseName)*.pyd")
    if ($extension.Count -ne 1) {
        throw "No se generó una única extensión compilada para $($tool.Name)."
    }
    $compiledTools += $extension[0].FullName
}

& $python -m nuitka `
    --standalone `
    @compilerOptions `
    --assume-yes-for-downloads `
    --enable-plugin=tk-inter `
    --windows-console-mode=disable `
    --windows-icon-from-ico=iconos\logo.ico `
    --company-name="Manuel Cobos Budia" `
    --product-name="Gestor de Proyectos" `
    --file-version=1.5.3 `
    --product-version=1.5.3 `
    --file-description="Gestión de presupuestos, planificación y certificaciones" `
    --output-dir=$nuitkaOutput `
    --output-filename=GestorDeProyectos.exe `
    --include-data-dir=iconos=iconos `
    --include-data-dir=plantillas=plantillas `
    --include-data-dir=datos=datos `
    --include-data-dir=archivos=archivos `
    --include-package-data=matplotlib `
    --include-package-data=pdfminer `
    --include-package-data=pypdfium2 `
    --include-package-data=pypdfium2_raw `
    --include-package=matplotlib `
    --include-package=openpyxl `
    --include-package=PIL `
    --include-package=pdfplumber `
    --include-package=pdfminer `
    --include-package=pypdfium2 `
    --include-package=pypdfium2_raw `
    --include-module=decimal `
    --include-module=calendar `
    --include-module=html `
    --include-module=hashlib `
    --include-module=queue `
    --include-module=threading `
    --include-module=uuid `
    --include-module=subprocess `
    --include-module=textwrap `
    --include-module=urllib.parse `
    --include-module=shutil `
    --include-module=matplotlib.backends.backend_agg `
    --include-module=matplotlib.backends.backend_pdf `
    --noinclude-unittest-mode=nofollow `
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
$installedTools = Join-Path $distributionDirectory "tools"
New-Item -ItemType Directory -Force -Path $installedTools | Out-Null
Get-ChildItem -LiteralPath $installedTools -Filter "*.pyd" -File |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
foreach ($extension in $compiledTools) {
    Copy-Item -LiteralPath $extension -Destination $installedTools -Force
}
& $python -c "from pathlib import Path; import sys; from tool_loader import discover_tools; loaded, errors = discover_tools(Path(sys.argv[1])); print('\n'.join(errors)); sys.exit(1 if errors else 0)" $installedTools
if ($LASTEXITCODE -ne 0) {
    throw "No se pueden cargar las herramientas compiladas de la distribución."
}

& $innoCompilerPath installer\GestorDeProyectos.iss
if ($LASTEXITCODE -ne 0) {
    throw "La creación del instalador con Inno Setup ha fallado."
}

Write-Host "Instalador creado en dist\GestorDeProyectos-Setup-1.5.3.exe"
