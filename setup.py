"""
Setup script for cluster-helper GPU SLO controller.

This setup script handles both Python package installation and
compilation of C extensions for NUMA utilities.
"""

from setuptools import setup, find_packages, Extension
from pathlib import Path
import sys

# Read README for long description
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text(encoding="utf-8") if readme_file.exists() else ""

# Read requirements
requirements_file = Path(__file__).parent / "requirements.txt"
if requirements_file.exists():
    with open(requirements_file) as f:
        requirements = [line.strip() for line in f if line.strip() and not line.startswith('#')]
else:
    requirements = [
        'systemd-python>=234',
        'psutil>=5.8.0',
        'numpy>=1.21.0',
        'pynvml>=11.4.1',
    ]

# Development requirements
dev_requirements = [
    'pytest>=6.2.0',
    'pytest-mock>=3.6.0',
    'flake8>=4.0.0',
    'mypy>=0.950',
]

# Define C extension for NUMA utilities
numa_utils_extension = Extension(
    'cluster_helper.native.numa_utils',
    sources=['src/cluster_helper/native/numa_utils.c'],
    libraries=['numa'],
    include_dirs=['/usr/include'],
    library_dirs=['/usr/lib', '/usr/lib64'],
)

# Check if libnuma is available
def check_numa_availability():
    """Check if libnuma development headers are available."""
    try:
        import subprocess
        result = subprocess.run(['pkg-config', '--exists', 'numa'], 
                              capture_output=True)
        return result.returncode == 0
    except FileNotFoundError:
        # pkg-config not available, try to find headers manually
        numa_header = Path('/usr/include/numa.h')
        return numa_header.exists()

# Conditionally include C extension
ext_modules = []
if check_numa_availability():
    ext_modules.append(numa_utils_extension)
    print("libnuma found, including NUMA utilities C extension")
else:
    print("Warning: libnuma not found, NUMA utilities will not be available")
    print("Install libnuma-dev (Ubuntu/Debian) or numactl-devel (RHEL/CentOS)")

setup(
    name="cluster-helper",
    version="1.0.0",
    author="Cluster-Helper Team",
    author_email="cluster-helper@example.com",
    description="SLO-aware GPU resource controller for multi-tenant workloads",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/riteshr19/Cluster-Helper",
    project_urls={
        "Bug Reports": "https://github.com/riteshr19/Cluster-Helper/issues",
        "Source": "https://github.com/riteshr19/Cluster-Helper",
    },
    
    # Package configuration
    packages=find_packages(where='src'),
    package_dir={'': 'src'},
    
    # C extensions
    ext_modules=ext_modules,
    
    # Dependencies
    install_requires=requirements,
    extras_require={
        'dev': dev_requirements,
        'test': ['pytest>=6.2.0', 'pytest-mock>=3.6.0'],
    },
    
    # Python version requirement
    python_requires='>=3.8',
    
    # Entry points
    entry_points={
        'console_scripts': [
            'gpu-controller=cluster_helper.main:run',
        ],
    },
    
    # Package metadata
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: System Administrators",
        "Intended Audience :: Developers",
        "Topic :: System :: Systems Administration",
        "Topic :: System :: Monitoring",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: C",
        "Operating System :: POSIX :: Linux",
    ],
    
    keywords="gpu cluster slo numa mig monitoring",
    
    # Include additional files
    include_package_data=True,
    package_data={
        'cluster_helper': ['*.conf'],
    },
    
    # Build requirements
    setup_requires=[
        'setuptools>=45',
        'wheel',
    ],
    
    # Development dependencies
    tests_require=dev_requirements,
    
    # Zip safety
    zip_safe=False,
)