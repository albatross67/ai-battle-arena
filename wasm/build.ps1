# Builds wasm/starter_bot.wasm from wasm/starter_bot.c (trusted sample only).
# Requires LLVM clang (winget install -e --id LLVM.LLVM). Run from the repo root.
$clang = if (Get-Command clang -ErrorAction SilentlyContinue) { "clang" }
         else { "C:\Program Files\LLVM\bin\clang.exe" }
& $clang --target=wasm32 -O2 -nostdlib `
    '-Wl,--no-entry' '-Wl,--export=obs_buf' '-Wl,--export=act_buf' `
    '-Wl,--export=init' '-Wl,--export=act' `
    -o "$PSScriptRoot\starter_bot.wasm" "$PSScriptRoot\starter_bot.c"
if ($LASTEXITCODE -eq 0) {
    $h = (Get-FileHash "$PSScriptRoot\starter_bot.wasm" -Algorithm SHA256).Hash.ToLower()
    Write-Output "built starter_bot.wasm  sha256=$h"
}
