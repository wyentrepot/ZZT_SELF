# GW HPLC DLL Build and Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install the minimum supported .NET Framework build environment, compile `GwHPLCAnalysis.dll`, and switch the local HPLC web debugger to that assembly.

**Architecture:** Keep the C# protocol parser as the source of truth and load its compiled assembly through Python.NET. The web application resolves the GW build output explicitly, exposes the loaded assembly version, and retains a fallback error when the expected assembly is absent.

**Tech Stack:** Visual Studio Build Tools 2022, MSBuild, .NET Framework 4.8, C#, Python 3, Python.NET, FastAPI, unittest.

## Global Constraints

- Target Windows and `.NET Framework 4.8`.
- The required output filename is exactly `GwHPLCAnalysis.dll`.
- The local web server must continue to listen only on `127.0.0.1`.
- Existing protocol parser behavior must be validated with automated tests before changing the web integration.
- Do not deploy the local DLL-backed application to a public host.

---

### Task 1: Install and verify the build toolchain

**Files:**
- Inspect: `dll/DLL_NwHPLCAnalysis.csproj`
- No repository files modified.

**Interfaces:**
- Consumes: Visual Studio Build Tools installer.
- Produces: a working `MSBuild.exe` capable of targeting `.NET Framework 4.8`.

- [ ] **Step 1: Detect existing toolchain**

Run:

```powershell
Get-Command msbuild -ErrorAction SilentlyContinue
Get-ChildItem "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe" -ErrorAction SilentlyContinue
```

Expected: either an existing MSBuild installation is found or installation is required.

- [ ] **Step 2: Install the minimum managed desktop build workload**

Run the official Visual Studio Build Tools installer with:

```text
Microsoft.VisualStudio.Workload.ManagedDesktopBuildTools
Microsoft.Net.Component.4.8.SDK
Microsoft.Net.Component.4.8.TargetingPack
```

Expected: installer exits successfully without requiring the full Visual Studio IDE.

- [ ] **Step 3: Verify MSBuild and the 4.8 reference assemblies**

Run:

```powershell
& $msbuildPath -version
Test-Path "${env:ProgramFiles(x86)}\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8"
```

Expected: MSBuild prints a version and the targeting-pack path returns `True`.

### Task 2: Restore dependencies and compile the GW assembly

**Files:**
- Inspect: `dll/DLL_NwHPLCAnalysis.csproj`
- Generated: `dll/bin/Debug/GwHPLCAnalysis.dll`

**Interfaces:**
- Consumes: `dll/DLL_NwHPLCAnalysis.csproj`, Newtonsoft.Json NuGet dependency.
- Produces: `dll/bin/Debug/GwHPLCAnalysis.dll`.

- [ ] **Step 1: Verify the project declares the required output**

Run:

```powershell
Select-String dll\DLL_NwHPLCAnalysis.csproj -Pattern '<AssemblyName>GwHPLCAnalysis</AssemblyName>'
```

Expected: one match.

- [ ] **Step 2: Restore NuGet packages**

Run:

```powershell
nuget restore DLL.sln
```

Expected: `packages/Newtonsoft.Json.13.0.4` or the project-compatible package path exists.

- [ ] **Step 3: Compile the library**

Run:

```powershell
& $msbuildPath dll\DLL_NwHPLCAnalysis.csproj /t:Rebuild /p:Configuration=Debug
```

Expected: build succeeds with zero errors and creates `dll/bin/Debug/GwHPLCAnalysis.dll`.

- [ ] **Step 4: Inspect the compiled assembly through Python.NET**

Run:

```powershell
python -c "import clr; clr.AddReference(r'dll\bin\Debug\GwHPLCAnalysis.dll'); from NW import NwHPLCAnalysis; print(NwHPLCAnalysis().GetProtocolVersion(None,None,None))"
```

Expected: the source version `V1.0.23` is returned.

### Task 3: Switch the web debugger to the GW assembly

**Files:**
- Modify: `hplc_web/app.py`
- Modify: `hplc_web/tests/test_dotnet_parser.py`
- Modify: `启动解析工具.bat`
- Test: `hplc_web/tests/test_app.py`
- Test: `hplc_web/tests/test_launcher.py`

**Interfaces:**
- Consumes: `dll/bin/Debug/GwHPLCAnalysis.dll`.
- Produces: FastAPI application backed by `NW.NwHPLCAnalysis` from the GW assembly.

- [ ] **Step 1: Write failing tests for the GW path**

Add assertions that the default DLL path and launcher preflight both reference:

```text
dll\bin\Debug\GwHPLCAnalysis.dll
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
python -m unittest hplc_web.tests.test_dotnet_parser hplc_web.tests.test_launcher -v
```

Expected: failure because the application and launcher still reference `dll_Tesll/NwHPLCAnalysis.dll`.

- [ ] **Step 3: Update the application and launcher**

Set the application default to:

```python
DEFAULT_DLL = BASE_DIR.parent / "dll" / "bin" / "Debug" / "GwHPLCAnalysis.dll"
```

Make the launcher check the same output path before starting the web service.

- [ ] **Step 4: Run focused tests**

Run:

```powershell
python -m unittest hplc_web.tests.test_dotnet_parser hplc_web.tests.test_launcher -v
```

Expected: all focused tests pass.

### Task 4: Verify the live parser and regression suite

**Files:**
- Test: `hplc_web/tests/test_parser_service.py`
- Test: `hplc_web/tests/test_app.py`

**Interfaces:**
- Consumes: compiled GW assembly and the provided `7E FF 02 ... 7E` frame.
- Produces: verified API response and documented remaining protocol-level result.

- [ ] **Step 1: Run all automated tests**

Run:

```powershell
python -m unittest discover -s hplc_web\tests -v
```

Expected: all tests pass with zero failures.

- [ ] **Step 2: Start the local service**

Run:

```powershell
python -m uvicorn hplc_web.app:app --host 127.0.0.1 --port 8765
```

Expected: service listens on `127.0.0.1:8765`.

- [ ] **Step 3: Verify assembly version**

Run:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/version
```

Expected: version information comes from `GwHPLCAnalysis.dll`.

- [ ] **Step 4: Submit the supplied frame**

POST the supplied complete frame to `/api/parse`.

Expected: the request reaches the GW assembly and returns either parsed JSON or an explicit protocol-level frame error without a web-layer JSON failure.

