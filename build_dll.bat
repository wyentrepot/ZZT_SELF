@echo off
chcp 65001 >nul
"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe" libs\shared\dll\DLL_NwHPLCAnalysis.csproj /p:Configuration=Debug /v:minimal
