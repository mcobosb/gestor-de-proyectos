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
$toolsSource = Join-Path $repositoryRoot "tools"
foreach ($requiredResource in @(
    "safety_health\datos\seguridad_salud_relaciones_bcca2023.csv",
    "pptp\BIBLIOTECA.zip",
    "pptp\docs\01_Introduccion.md",
    "pptp\docs\02_DisposicionesTecnicasLegalesAplicables.md",
    "pptp\docs\03_DescripcionObras.md",
    "pptp\docs\04_Materiales_Ejecucion_Medicion_Abono.md"
)) {
    $resourcePath = Join-Path $toolsSource $requiredResource
    if (-not (Test-Path -LiteralPath $resourcePath -PathType Leaf)) {
        throw "Falta un recurso de las herramientas: $resourcePath"
    }
}
& $python -c "from pathlib import Path; import sys; from tools.pptp.library import ZipLibrary; root = Path(sys.argv[1]) / 'pptp'; library = ZipLibrary(root / 'BIBLIOTECA.zip', root / 'docs'); print('Fichas PPTP:', len(library.entries))" $toolsSource
if ($LASTEXITCODE -ne 0) {
    throw "La biblioteca comprimida PPTP no es válida."
}
foreach ($tool in Get-ChildItem $toolsSource -Filter "*.py" -Recurse -File) {
    $relativePath = $tool.FullName.Substring($toolsSource.Length + 1)
    $relativeDirectory = Split-Path $relativePath -Parent
    $isPackage = $tool.Name -eq "__init__.py"
    if ($isPackage -and -not $relativeDirectory) {
        continue
    }
    $moduleName = $tool.BaseName
    $compileTarget = $tool.FullName
    if ($isPackage) {
        $moduleName = Split-Path $tool.DirectoryName -Leaf
        $compileTarget = $tool.DirectoryName
    }
    $moduleOutput = Join-Path $toolsOutput $relativePath.Replace(".py", "")
    if (Test-Path $moduleOutput) {
        Get-ChildItem -LiteralPath $moduleOutput -Filter "$moduleName*.pyd" -File |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
    }
    & $python -m nuitka `
        --module `
        --nofollow-imports `
        @compilerOptions `
        --assume-yes-for-downloads `
        --output-dir=$moduleOutput `
        $compileTarget
    if ($LASTEXITCODE -ne 0) {
        throw "La compilación de la herramienta $($tool.Name) ha fallado."
    }
    $extension = @(Get-ChildItem $moduleOutput -Filter "$moduleName*.pyd")
    if ($extension.Count -ne 1) {
        throw "No se generó una única extensión compilada para $($tool.Name)."
    }
    $extensionName = $extension[0].Name
    if ($isPackage) {
        $extensionName = "__init__" + $extensionName.Substring($moduleName.Length)
    }
    $relativeDestination = $extensionName
    if ($relativeDirectory) {
        $relativeDestination = Join-Path $relativeDirectory $extensionName
    }
    $compiledTools += [pscustomobject]@{
        Source = $extension[0].FullName
        Destination = $relativeDestination
    }
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
Get-ChildItem -LiteralPath $installedTools -Filter "*.pyd" -Recurse -File |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
foreach ($extension in $compiledTools) {
    $destination = Join-Path $installedTools $extension.Destination
    New-Item -ItemType Directory -Force -Path (Split-Path $destination -Parent) | Out-Null
    Copy-Item -LiteralPath $extension.Source -Destination $destination -Force
}
foreach ($resource in Get-ChildItem $toolsSource -Recurse -File) {
    if ($resource.Name -eq ".gitkeep" -or
        $resource.Extension -in @(".py", ".pyc", ".pyo", ".pyd") -or
        $resource.FullName -match '[\\/]__pycache__[\\/]') {
        continue
    }
    $relativePath = $resource.FullName.Substring($toolsSource.Length + 1)
    $destination = Join-Path $installedTools $relativePath
    New-Item -ItemType Directory -Force -Path (Split-Path $destination -Parent) | Out-Null
    Copy-Item -LiteralPath $resource.FullName -Destination $destination -Force
}
& $python -c "from pathlib import Path; import sys; from tool_loader import discover_tools; loaded, errors = discover_tools(Path(sys.argv[1])); expected = {p.name for p in Path(sys.argv[2]).iterdir() if p.is_dir() and not p.name.startswith('_') and (p / '__init__.py').is_file()}; actual = {tool.name for tool in loaded}; print('\n'.join(errors)); print('Esperadas:', sorted(expected), 'Encontradas:', sorted(actual)); sys.exit(1 if errors or actual != expected else 0)" $installedTools $toolsSource
if ($LASTEXITCODE -ne 0) {
    throw "No se pueden cargar las herramientas compiladas de la distribución."
}

& $innoCompilerPath installer\GestorDeProyectos.iss
if ($LASTEXITCODE -ne 0) {
    throw "La creación del instalador con Inno Setup ha fallado."
}

Write-Host "Instalador creado en dist\GestorDeProyectos-Setup-1.5.3.exe"
