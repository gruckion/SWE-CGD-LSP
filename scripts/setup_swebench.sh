#!/bin/bash
# Setup script for SWE-bench integration

set -e

echo "=== SWE-CGD Setup Script ==="

# Check prerequisites
echo "Checking prerequisites..."

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is not installed. Please install Docker first."
    exit 1
fi

if ! docker run --rm hello-world &> /dev/null; then
    echo "ERROR: Docker is not running properly. Please check your Docker installation."
    exit 1
fi

echo "Docker: OK"

# Check Python
if ! command -v python &> /dev/null; then
    echo "ERROR: Python is not installed."
    exit 1
fi

PYTHON_VERSION=$(python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python: $PYTHON_VERSION"

# Create virtual environment if not exists
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python -m venv .venv
fi

echo "Activating virtual environment..."
source .venv/bin/activate

# Upgrade pip
pip install -U pip

# Install this package
echo "Installing swe-cgd-lsp..."
pip install -e .

# Clone and install SWE-bench if not present
SWEBENCH_DIR="${SWEBENCH_DIR:-../SWE-bench}"

if [ ! -d "$SWEBENCH_DIR" ]; then
    echo "Cloning SWE-bench..."
    git clone https://github.com/SWE-bench/SWE-bench.git "$SWEBENCH_DIR"
fi

echo "Installing SWE-bench..."
pip install -e "$SWEBENCH_DIR"

# Validate installation
echo ""
echo "=== Validating Installation ==="

# Check SWE-bench harness
if python -c "import swebench" 2>/dev/null; then
    echo "SWE-bench: OK"
else
    echo "WARNING: SWE-bench import failed"
fi

# Check pyright
if command -v pyright &> /dev/null || python -m pyright --version &> /dev/null; then
    echo "Pyright: OK"
else
    echo "Installing pyright..."
    pip install pyright
fi

# Check disk space
DISK_FREE=$(df -BG . | tail -1 | awk '{print $4}' | tr -d 'G')
echo "Free disk space: ${DISK_FREE}GB"
if [ "$DISK_FREE" -lt 50 ]; then
    echo "WARNING: Less than 50GB free disk space. SWE-bench images can be large."
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "1. Activate the environment: source .venv/bin/activate"
echo "2. Set your API key: export ANTHROPIC_API_KEY=your-key"
echo "3. Validate with gold patch:"
echo "   python -m swebench.harness.run_evaluation \\"
echo "     --predictions_path gold \\"
echo "     --max_workers 1 \\"
echo "     --instance_ids sympy__sympy-20590 \\"
echo "     --run_id validate-gold"
echo ""
echo "4. Run the CGD experiment:"
echo "   swe-cgd run-pipeline --max 10 --skip-eval"
