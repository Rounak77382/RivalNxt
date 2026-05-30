# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
import os
from pathlib import Path

datas = []
datas += collect_data_files('core.db.migrations')

# Include character_ids.json for entity tagging
character_ids_path = os.path.join('.', 'character_ids.json')
if os.path.exists(character_ids_path):
    datas.append((character_ids_path, '.'))

# Include backend icon for executable
backend_icon_path = os.path.join('.', 'src-tauri', 'icons', 'backendicon.ico')
if os.path.exists(backend_icon_path):
    datas.append((backend_icon_path, 'src-tauri/icons'))

# Include C++ optimizations
fast_locres_path = os.path.join('.', 'src-cpp', 'fast_locres.exe')
if os.path.exists(fast_locres_path):
    datas.append((fast_locres_path, 'src-cpp'))

# Include bundled 7za
bin_dir = Path('bin')
if bin_dir.exists():
    datas.append((str(bin_dir), 'bin'))

# Add the entire core directory as data so it's available at runtime
core_dir = Path('core')
if core_dir.exists():
    datas.append((str(core_dir), 'core'))

# Add the entire scripts directory as data so it's available at runtime
scripts_dir = Path('scripts')
if scripts_dir.exists():
    datas.append((str(scripts_dir), 'scripts'))

# Add PyO3 Rust library extracted files as data
extracted_wheel_dir = Path('extracted_wheel/rust_ue_tools')
if extracted_wheel_dir.exists():
    datas.append((str(extracted_wheel_dir), 'rust_ue_tools'))

# Add root-level Python files as data
root_py_files = ['field_prefs.py', 'build_rust_pyo3.py']
for py_file in root_py_files:
    file_path = Path(py_file)
    if file_path.exists():
        datas.append((str(file_path), '.'))

# Auto-discover all core and scripts submodules, plus manual additional imports
_hiddenimports = collect_submodules('core')
_hiddenimports += collect_submodules('scripts')

# Add additional specific imports that might be needed
_hiddenimports.extend([
    'py7zr',
    'rarfile',
    'fastapi.middleware',
    'fastapi.middleware.cors',
    'fastapi.middleware.trustedhost',
    'fastapi.middleware.gzip',
    'fastapi.responses',
    'fastapi.routing',
    'fastapi.applications',
    'fastapi.dependencies',
    'uvicorn',
    'requests',
    'python_multipart',
    'pydantic',
    'psutil',
    'rust_ue_tools',
    'pylocres'
])

# Add the project root to pathex so PyInstaller can find the modules
_project_root = os.path.abspath('.')

a = Analysis(
    ['src-python/run_server.py'],
    pathex=[_project_root],
    binaries=[],
    datas=datas,
    hiddenimports=_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Qt frameworks
        'PyQt5', 'PyQt6', 'PySide6',
        # Machine Learning / Deep Learning (HUGE)
        'tensorflow', 'torch', 'torchvision', 'torchaudio', 'tensorboard',
        'tensorflow_intel', 'tensorflow_estimator', 'tensorboard_data_server',
        'numpy.distutils', 'numpy.f2py',
        # Data Science / Visualization
        'matplotlib', 'seaborn', 'plotly', 'bokeh', 'altair',
        'scipy.optimize', 'scipy.integrate', 'scipy.interpolate',
        'sklearn', 'scikit-learn', 'scikit-image',
        'pandas.tests', 'statsmodels',
        # Web Scraping / Automation
        'selenium', 'scrapy', 'beautifulsoup4',
        # Jupyter / IPython
        'IPython', 'jupyter', 'notebook', 'ipykernel', 'ipywidgets',
        # Other heavy libraries
        'streamlit', 'dash', 'flask', 'django',
        'cv2', 'PIL.ImageQt',
        # Testing frameworks
        'pytest', 'unittest.mock', 'doctest',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='rivalnxt_backend',
    icon=os.path.join(_project_root, 'src-tauri', 'icons', 'backendicon.ico'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)