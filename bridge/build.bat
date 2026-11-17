@echo off
rem Build MCPBRIDGE.DLL (32-bit, Proteus 8 is a 32-bit program).
rem Needs: Visual Studio 2019/2022 Build Tools (C++), and Labcenter's vsm.hpp.
rem vsm.hpp is Labcenter's copyrighted header and is NOT shipped here.
rem Put it in bridge\sdk\vsm.hpp, or set VSM_SDK to the folder that holds it.
setlocal
if "%VSM_SDK%"=="" set VSM_SDK=%~dp0sdk
if not exist "%VSM_SDK%\vsm.hpp" (
  echo vsm.hpp not found in "%VSM_SDK%". See bridge\README.md.
  exit /b 1
)
for /f "usebackq delims=" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set VSDIR=%%i
if "%VSDIR%"=="" ( echo Visual Studio C++ build tools not found. & exit /b 1 )
call "%VSDIR%\VC\Auxiliary\Build\vcvars32.bat" >nul
cd /d "%~dp0"
cl /nologo /LD /EHsc /O2 /MT /W3 /I "%VSM_SDK%" mcpbridge.cpp /Fe:MCPBRIDGE.DLL /link /MACHINE:X86 || exit /b 1
del /q mcpbridge.obj MCPBRIDGE.exp MCPBRIDGE.lib 2>nul
echo Built %~dp0MCPBRIDGE.DLL
