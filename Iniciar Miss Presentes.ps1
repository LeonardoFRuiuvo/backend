$ErrorActionPreference = 'Stop'
$backendDirectory = $PSScriptRoot
$pythonPath = Join-Path $backendDirectory '.venv\Scripts\python.exe'
$requirementsPath = Join-Path $backendDirectory 'requirements.txt'
$frontendIndex = Join-Path $backendDirectory 'static\frontend\index.html'
$baseUrl = 'http://127.0.0.1:8000'
$healthUrl = "$baseUrl/api/v1/health/"

function Test-PdvServer {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        return $health.data.status -eq 'ok'
    }
    catch {
        return $false
    }
}

function Invoke-CheckedCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$FailureMessage
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

try {
    if (-not (Test-Path $frontendIndex)) {
        throw 'A interface ainda nao foi compilada. Gere o pacote integrado antes de iniciar.'
    }

    if (Test-PdvServer) {
        Start-Process $baseUrl
        exit 0
    }

    $createdEnvironment = $false
    if (-not (Test-Path $pythonPath)) {
        $pythonLauncher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
        if (-not $pythonLauncher) {
            throw 'Python 3.12 nao encontrado. Instale o Python 3.12 e tente novamente.'
        }

        Invoke-CheckedCommand -FilePath $pythonLauncher.Source `
            -Arguments @('-3.12', '-m', 'venv', (Join-Path $backendDirectory '.venv')) `
            -FailureMessage 'Nao foi possivel preparar o ambiente Python do PDV.'
        $createdEnvironment = $true
    }

    if (-not (Test-Path $pythonPath)) {
        throw 'O ambiente Python local nao foi criado corretamente.'
    }

    if ($createdEnvironment) {
        Invoke-CheckedCommand -FilePath $pythonPath `
            -Arguments @('-m', 'pip', 'install', '-r', $requirementsPath) `
            -FailureMessage 'Nao foi possivel instalar as dependencias do backend. Verifique a conexao com a internet.'
    }
    else {
        & $pythonPath -c 'import django, dotenv, PIL' 2>$null
        if ($LASTEXITCODE -ne 0) {
            Invoke-CheckedCommand -FilePath $pythonPath `
                -Arguments @('-m', 'pip', 'install', '-r', $requirementsPath) `
                -FailureMessage 'Nao foi possivel instalar as dependencias do backend. Verifique a conexao com a internet.'
        }
    }

    $envFile = Join-Path $backendDirectory '.env'
    if (-not (Test-Path $envFile)) {
        $exampleFile = Join-Path $backendDirectory '.env.example'
        if (-not (Test-Path $exampleFile)) {
            throw 'Arquivo .env.example nao encontrado; nao foi possivel configurar o backend.'
        }
        $secret = (& $pythonPath -c 'import secrets; print(secrets.token_urlsafe(50))').Trim()
        if ($LASTEXITCODE -ne 0 -or -not $secret) {
            throw 'Nao foi possivel gerar a chave local do Django.'
        }
        $environment = (Get-Content $exampleFile -Raw).Replace(
            'replace-with-a-random-secret-key',
            $secret
        )
        Set-Content -Path $envFile -Value $environment -Encoding UTF8
    }

    $env:DJANGO_DEBUG = 'True'
    $env:DJANGO_ALLOWED_HOSTS = '127.0.0.1,localhost'
    $env:DJANGO_CSRF_TRUSTED_ORIGINS = 'http://127.0.0.1:8000'

    Invoke-CheckedCommand -FilePath $pythonPath `
        -Arguments @('manage.py', 'migrate', '--noinput') `
        -FailureMessage 'Nao foi possivel preparar o banco de dados do PDV.'

    $serverCommand = 'title Miss Presentes - servidor local & "{0}" manage.py runserver 127.0.0.1:8000 --noreload' -f $pythonPath
    Start-Process -FilePath $env:ComSpec `
        -ArgumentList @('/c', $serverCommand) `
        -WorkingDirectory $backendDirectory `
        -WindowStyle Minimized

    $serverReady = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if (Test-PdvServer) {
            $serverReady = $true
            break
        }
        Start-Sleep -Seconds 1
    }

    if (-not $serverReady) {
        throw 'O servidor nao respondeu. Restaure a janela minimizada do servidor para verificar o erro.'
    }

    Start-Process $baseUrl
}
catch {
    Write-Host ''
    Write-Host "Nao foi possivel iniciar o PDV: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
