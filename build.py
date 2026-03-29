import os
import sys
import shutil
import subprocess
from pathlib import Path

def build():
    # ── Project setup ────────────────────────────────────────────────────────
    PROJECT_ROOT = Path(__file__).resolve().parent
    dist_dir = PROJECT_ROOT / "dist"
    build_dir = PROJECT_ROOT / "build"
    assets_dir = PROJECT_ROOT / "assets"
    
    executable_name = "DroidTracePro"
    main_script = PROJECT_ROOT / "main.py"
    
    print(f"Starting build of {executable_name}...")
    
    # ── Verify requirements ──────────────────────────────────────────────────
    if not main_script.exists():
        print(f"Error: Could not find main script: {main_script}")
        return
        
    adb_exe = assets_dir / "adb" / "adb.exe"
    if not adb_exe.exists():
        print(f"Warning: Bundled adb.exe not found at {adb_exe}")
        print("   The build will proceed, but ADB must be in the system PATH to function.")

    # ── Clean previous builds ────────────────────────────────────────────────
    for d in [dist_dir, build_dir]:
        if d.exists():
            print(f"Cleaning {d.name} folder...")
            shutil.rmtree(d, ignore_errors=True)

    # ── PyInstaller configuration ───────────────────────────────────────────
    # We use --onefile for a single portable EXE
    # We use --windowed (or --noconsole) to suppress the terminal window
    
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        f"--name={executable_name}",
        f"--add-data=assets{os.pathsep}assets",
        f"--add-data=core{os.pathsep}core",
        f"--add-data=models{os.pathsep}models",
        f"--add-data=utils{os.pathsep}utils",
        f"--add-data=config{os.pathsep}config",
        f"--add-data=ui{os.pathsep}ui",
        str(main_script)
    ]
    
    print(f"Running command: {' '.join(cmd)}")
    
    try:
        subprocess.check_call(cmd)
        print("\n" + "="*60)
        print(f"BUILD COMPLETE: {dist_dir / (executable_name + '.exe')}")
        print("="*60)
    except subprocess.CalledProcessError as e:
        print(f"\nError: Build failed with exit code {e.returncode}")
        sys.exit(e.returncode)

if __name__ == "__main__":
    build()
