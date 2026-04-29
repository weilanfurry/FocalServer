$ErrorActionPreference = "Stop"

function Resolve-Python {
  $candidates = @(
    @{ Name = "python"; Args = @("--version") },
    @{ Name = "py"; Args = @("-3", "--version") },
    @{ Name = "python3"; Args = @("--version") }
  )

  foreach ($c in $candidates) {
    $cmd = Get-Command $c.Name -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    try {
      & $cmd.Source @($c.Args) *> $null
      return @{ Command = $cmd.Source; Name = $c.Name }
    } catch {
      continue
    }
  }

  return $null
}

function Write-StdErr([string]$message) {
  [Console]::Error.WriteLine($message)
}

$root = $PSScriptRoot
Set-Location $root

$py = Resolve-Python
if (-not $py) {
  Write-StdErr "未检测到可用的 Python（python/py/python3）。"
  Write-StdErr "请先安装 Python 3.10+（安装时勾选 Add python.exe to PATH），然后重新运行："
  Write-StdErr "  .\\start.ps1"
  exit 1
}

$systemPythonExe = $null
try {
  if ($py.Name -eq "py") {
    $systemPythonExe = (& $py.Command -3 -c "import sys;print(sys.executable)").Trim()
  } else {
    $systemPythonExe = (& $py.Command -c "import sys;print(sys.executable)").Trim()
  }
} catch {
  $systemPythonExe = $null
}
if ($systemPythonExe) {
  $env:SKILLBOTTLE_SYSTEM_PYTHON = $systemPythonExe
}

$venvDir = Join-Path $root ".venv"
$venvPython = Join-Path $venvDir "Scripts\\python.exe"

function New-Venv {
  if ($py.Name -eq "py") {
    & $py.Command -3 -m venv $venvDir
  } else {
    & $py.Command -m venv $venvDir
  }
}

if (-not (Test-Path $venvPython)) {
  Write-Output "创建虚拟环境：$venvDir"
  New-Venv
}

try {
  & $venvPython -c "import sys; print(sys.executable)" *> $null
} catch {
  Write-Output "检测到虚拟环境已损坏，重新创建：$venvDir"
  Remove-Item -LiteralPath $venvDir -Recurse -Force -ErrorAction SilentlyContinue
  New-Venv
}

Write-Output "安装依赖：requirements.txt"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $root "requirements.txt")

Write-Output "启动 SkillBottle Lite：http://127.0.0.1:8000/"
& $venvPython -m uvicorn main:app --reload --port 8000
