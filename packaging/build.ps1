# CampusStream Helper - Windows 打包脚本
#
# 用法（在仓库根目录或任意位置）：
#   .\packaging\build.ps1
#   .\packaging\build.ps1 -SkipTests
#
# 产物：dist\CampusStreamHelper.exe（单文件，无需安装 Python）

[CmdletBinding()]
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Spec = Join-Path $Root "packaging\campusstream_helper.spec"
$Exe = Join-Path $Root "dist\CampusStreamHelper.exe"

if (-not (Test-Path $Python)) {
    throw "未找到虚拟环境：$Python。请先按 README「开发者」步骤创建 .venv。"
}
if (-not (Test-Path $Spec)) {
    throw "未找到 PyInstaller 配置：$Spec"
}

Push-Location $Root
try {
    if (-not $SkipTests) {
        Write-Host "==> 运行测试（离屏模式）" -ForegroundColor Cyan
        $env:QT_QPA_PLATFORM = "offscreen"
        & $Python -m unittest discover -s tests -v
        if ($LASTEXITCODE -ne 0) {
            throw "测试未通过，已中止打包。"
        }
        Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    }

    Write-Host "==> 清理旧的构建产物" -ForegroundColor Cyan
    Remove-Item -Recurse -Force (Join-Path $Root "build") -ErrorAction SilentlyContinue
    Remove-Item -Force $Exe -ErrorAction SilentlyContinue

    Write-Host "==> PyInstaller 打包中（首次可能需要 1-3 分钟）" -ForegroundColor Cyan
    & $Python -m PyInstaller --noconfirm --clean $Spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 构建失败。"
    }
    if (-not (Test-Path $Exe)) {
        throw "构建结束但未找到 $Exe"
    }

    Write-Host "==> 冒烟测试打包后的程序" -ForegroundColor Cyan
    $proc = Start-Process -FilePath $Exe -ArgumentList "--smoke-test" -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "打包后的程序冒烟测试失败（退出码 $($proc.ExitCode)）。"
    }

    $version = (& $Python -c "from core import __version__; print(__version__)").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $version) {
        throw "无法读取 core.__version__。"
    }
    $releaseExe = Join-Path $Root "dist\CampusStreamHelper-v$version.exe"
    Copy-Item -Force $Exe $releaseExe

    $item = Get-Item $releaseExe
    $sizeMb = [math]::Round($item.Length / 1MB, 1)
    $hash = (Get-FileHash $releaseExe -Algorithm SHA256).Hash

    Write-Host ""
    Write-Host "构建成功" -ForegroundColor Green
    Write-Host "  文件：$releaseExe"
    Write-Host "  体积：$sizeMb MB"
    Write-Host "  SHA256：$hash"
    Write-Host ""
    Write-Host "下一步：运行该 exe 手动验证，再把文件和 SHA256 一起发布到 GitHub Releases。"
}
finally {
    Pop-Location
}
