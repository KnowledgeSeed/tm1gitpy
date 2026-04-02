import os
from pathlib import Path
from setuptools import find_packages, setup

PACKAGE_ROOT = Path(__file__).resolve().parent
README_PATH = PACKAGE_ROOT / "README.md"

if README_PATH.exists():
    long_description = README_PATH.read_text(encoding="utf-8")
    long_description_content_type = "text/markdown"
else:
    long_description = "Utilities for exporting and comparing TM1 models in Git-friendly formats."
    long_description_content_type = "text/plain"

def read_version():
    version_file = os.path.join(os.path.dirname(__file__), 'tm1_git_py', '__init__.py')
    with open(version_file, 'r') as f:
        for line in f:
            if line.startswith('__version__'):
                delim = '"' if '"' in line else "'"
                return line.split(delim)[1]
    raise RuntimeError("Unable to find version string.")

setup(
    name="tm1gitpy",
    version=read_version(),
    description="Utilities for exporting and comparing TM1 models for Git workflows.",
    long_description=long_description,
    long_description_content_type=long_description_content_type,
    author="",
    keywords = ["tm1", "ibm-planning-analytics", "model-export", "devops", "git", "version-control"],
    classifiers=[
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Version Control :: Git",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: OS Independent",
    ],
    packages=find_packages(),
    include_package_data=True,
    package_data={
        "tm1_git_py": ["*.txt", "*.json"],
    },
    install_requires=[
        "TM1py>=2.1,<3.0",
        "requests>=2.25",
        "PyYAML>=6.0",
        "orjson",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "pytest-mock",
            "testcontainers>=4.0.0",
            "orjson",
        ],
    },
    python_requires=">=3.10",
)
