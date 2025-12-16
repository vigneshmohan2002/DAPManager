
import os
import platform
import shutil
import zipfile
import logging
import stat

logger = logging.getLogger(__name__)

def detect_platform():
    """
    Returns (os_name, arch_name) compatible with sldl release naming.
    OS: 'linux', 'osx' (macos), 'win' (windows)
    Arch: 'x64', 'arm64', 'x86'
    """
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    os_name = ""
    if system == "linux":
        os_name = "linux"
    elif system == "darwin":
        os_name = "osx"
    elif system == "windows":
        os_name = "win"
    else:
        raise Exception(f"Unsupported OS: {system}")
        
    arch_name = ""
    if "arm" in machine or "aarch64" in machine:
        if "64" in machine:
            arch_name = "arm64" 
            if os_name == "linux": arch_name = "arm" # Linux release is just 'linux-arm'
        else:
            arch_name = "arm"
    elif "64" in machine:
        arch_name = "x64"
    elif "86" in machine:
        arch_name = "x86"
        
    # Validation adjustment for specific release names
    # Release names: 
    # sldl_linux-arm.zip
    # sldl_linux-x64.zip
    # sldl_osx-arm64.zip
    # sldl_osx-x64.zip
    # sldl_win-x86.zip (There is no x64 specific for win, x86 runs on x64)
    # sldl_win-x86_self-contained.zip
    
    if os_name == "win":
        arch_name = "x86" # Always use x86 for windows as per releases
        
    return os_name, arch_name

def install_from_local(releases_dir: str, target_bin_dir: str):
    """
    Scans releases_dir for the matching zip, extracts it to target_bin_dir/slsk-batchdl.
    Returns the full path to the executable.
    """
    os_name, arch = detect_platform()
    logger.info(f"Detected platform: {os_name}-{arch}")
    
    # Construct expected zip name pattern
    # e.g. sldl_osx-arm64.zip
    expected_zip = f"sldl_{os_name}-{arch}.zip"
    
    zip_path = os.path.join(releases_dir, expected_zip)
    
    # Fallback/Fuzzy check if exact match not found
    if not os.path.exists(zip_path):
        logger.warning(f"Exact match {expected_zip} not found in {releases_dir}")
        
        # Windows Self-Contained Fallback
        if os_name == "win":
            alt_zip = f"sldl_{os_name}-{arch}_self-contained.zip"
            zip_path = os.path.join(releases_dir, alt_zip)
            
        # MacOS ARM64 -> x64 Fallback (Rosetta 2)
        elif os_name == "osx" and arch == "arm64":
            logger.info("MACOS ARM64 detected but zip missing. Attempting fallback to x64 (Rosetta 2)...")
            alt_zip = f"sldl_{os_name}-x64.zip"
            zip_path = os.path.join(releases_dir, alt_zip)

    if not os.path.exists(zip_path):
        raise FileNotFoundError(f"Could not find release file: {zip_path}. Please place it in {releases_dir}.")
        
    logger.info(f"Found release archive: {zip_path}")
    
    # Prepare extraction
    temp_dir = os.path.join(target_bin_dir, "temp_extract")
    if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
    os.makedirs(temp_dir, exist_ok=True)
    
    # Unzip
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(temp_dir)
        
    # Find binary
    binary_name = "sldl.exe" if os_name == "win" else "sldl"
    source_bin = None
    
    for root, dirs, files in os.walk(temp_dir):
        if binary_name in files:
            source_bin = os.path.join(root, binary_name)
            break
            
    if not source_bin:
        raise Exception(f"Could not find '{binary_name}' inside the zip file.")
        
    # Move to final location
    final_bin_name = "slsk-batchdl"
    if os_name == "win": final_bin_name += ".exe"
    
    os.makedirs(target_bin_dir, exist_ok=True)
    final_path = os.path.join(target_bin_dir, final_bin_name)
    
    # Remove existing
    if os.path.exists(final_path):
        os.remove(final_path)
        
    shutil.move(source_bin, final_path)
    
    # Cleanup
    shutil.rmtree(temp_dir)
    
    # Make executable (Unix)
    if os_name != "win":
        st = os.stat(final_path)
        os.chmod(final_path, st.st_mode | stat.S_IEXEC)
        
    logger.info(f"Successfully installed to: {final_path}")
    return final_path
