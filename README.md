# Cluster-Helper

[![CI](https://github.com/riteshr19/Cluster-Helper/actions/workflows/ci.yml/badge.svg)](https://github.com/riteshr19/Cluster-Helper/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

**Cluster-Helper** is a production-grade, SLO-aware GPU resource controller designed for multi-tenant GPU workloads on NVIDIA A100/H100 systems. It intelligently monitors tenant performance and applies adaptive isolation actions when SLO violations are detected due to "noisy neighbors."

## Features

- **🎯 SLO-Aware Monitoring**: Continuously monitors p99 latency metrics for tenant workloads
- **🔍 Intelligent Detection**: Identifies SLO violations with configurable persistence thresholds  
- **⚡ Adaptive Mitigation**: Applies tiered response strategies including cgroup guardrails and MIG reconfiguration
- **🗺️ Topology-Aware**: Uses NUMA and PCIe topology information for optimal workload placement
- **🛡️ Production-Ready**: Comprehensive logging, systemd integration, and robust error handling
- **🧪 Extensively Tested**: High test coverage with mocked hardware dependencies

## Architecture

### Core Components

1. **Configuration Manager** (`config.py`): Parses and validates configuration from `/etc/gpu-controller.conf`
2. **Topology Manager** (`topology.py`): Discovers NUMA and PCIe topology using `lspci` and `hwloc`
3. **Metrics Monitor** (`metrics.py`): Collects tenant latency metrics from `/var/run/tenant_metrics/`
4. **State Manager** (`state.py`): Tracks violation states and implements cooldown periods
5. **Action Executor** (`actions.py`): Executes mitigation actions (cgroups, MIG reconfiguration)
6. **Main Daemon** (`main.py`): Orchestrates the monitoring and mitigation loop

### Mitigation Strategy

**Tier 1: Cgroup I/O Limiting**
- Applies bandwidth limits to processes identified as "bullies"
- Severity-based limit calculation
- Non-disruptive to victim workloads

**Tier 2: MIG Reconfiguration** 
- Reconfigures GPU MIG profiles for stronger isolation
- Triggered for high-severity violations
- Topology-aware instance allocation

## Installation

### Prerequisites

- Linux system with NVIDIA GPUs (A100/H100 recommended)
- Python 3.8+
- NVIDIA drivers and CUDA toolkit
- libnuma development headers
- Root privileges for cgroup and MIG operations

### Install System Dependencies

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y build-essential libnuma-dev hwloc nvidia-utils-460

# RHEL/CentOS
sudo yum install -y gcc libnuma-devel hwloc nvidia-driver
```

### Install Cluster-Helper

```bash
# From source
git clone https://github.com/riteshr19/Cluster-Helper.git
cd Cluster-Helper
pip install -e .

# Or install from PyPI (when available)
pip install cluster-helper
```

## Configuration

Create `/etc/gpu-controller.conf`:

```ini
[controller]
# SLO threshold for p99 latency (milliseconds)
tail_threshold_ms = 100.0

# Number of consecutive violations before taking action
persistence_windows = 3

# Cooldown period (observation cycles) before re-evaluating same tenant
cooldown_observations = 10

# Monitoring interval (seconds)
poll_interval_sec = 30.0

# Logging level
log_level = INFO

[placement]
# NUMA affinity penalty weight
numa_weight = 2.0

# PCIe topology penalty weight  
pcie_weight = 1.5

# Enable MIG reconfiguration
enable_mig_reconfiguration = true

# Maximum I/O limit via cgroups (MB/s)
max_cgroup_io_limit_mbps = 1000
```

## Usage

### Running as a Daemon

```bash
# Install systemd service
sudo cp scripts/gpu-controller.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable gpu-controller
sudo systemctl start gpu-controller

# Check status
sudo systemctl status gpu-controller
sudo journalctl -u gpu-controller -f
```

### Manual Execution

```bash
# Run with default config
sudo gpu-controller

# Run with custom config
sudo gpu-controller --config /path/to/config.conf

# Enable debug logging
sudo gpu-controller --debug
```

### Tenant Metric Integration

Tenants must write their p99 latency metrics to `/var/run/tenant_metrics/<PID>.metric`:

```bash
# Example metric file format
echo "p99_latency_ms: 87.5" > /var/run/tenant_metrics/$(echo $$).metric

# Or simple numeric format
echo "87.5" > /var/run/tenant_metrics/$(echo $$).metric
```

## Development

### Setup Development Environment

```bash
git clone https://github.com/riteshr19/Cluster-Helper.git
cd Cluster-Helper

# Install in development mode
pip install -e .[dev]

# Install pre-commit hooks
pre-commit install
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=cluster_helper --cov-report=html

# Run specific test module
pytest tests/test_state.py -v
```

### Building C Extensions

```bash
# Build C extension in-place
python setup.py build_ext --inplace

# Clean build artifacts
python setup.py clean --all
```

### Code Quality

```bash
# Linting
flake8 src/

# Type checking
mypy src/cluster_helper/

# Format code
black src/ tests/
```

## API Reference

### Configuration

```python
from cluster_helper.config import ControllerConfig

config = ControllerConfig('/etc/gpu-controller.conf')
print(f"SLO threshold: {config.tail_threshold_ms}ms")
```

### Topology Discovery

```python
from cluster_helper.topology import TopologyManager

topology = TopologyManager()
gpus = topology.list_gpus()
affinity_score = topology.get_affinity_score(gpus[0], gpus[1])
```

### State Management

```python
from cluster_helper.state import StateManager

state_manager = StateManager(tail_threshold_ms=100.0)
violations = state_manager.update({1234: 150.0})  # PID: latency_ms
```

### Action Execution

```python
from cluster_helper.actions import ActionExecutor

executor = ActionExecutor(enable_mig=True)
results = executor.mitigate_violation(violation)
```

## Monitoring and Observability

### Logs

Cluster-Helper logs to systemd journal with structured formatting:

```bash
# View recent logs
sudo journalctl -u gpu-controller --since "1 hour ago"

# Follow logs in real-time
sudo journalctl -u gpu-controller -f

# Filter by log level
sudo journalctl -u gpu-controller -p err
```

### Metrics

Monitor these key metrics:

- **Tenant States**: Normal, Degraded, Violated, Cooldown
- **Action Success Rates**: Cgroup applications, MIG reconfigurations
- **Violation Frequency**: Trends in SLO violations
- **Topology Utilization**: GPU affinity scores

### Health Checks

```bash
# Check service status
sudo systemctl is-active gpu-controller

# Verify configuration
gpu-controller --config /etc/gpu-controller.conf --dry-run

# Test NUMA extension
python -c "from cluster_helper.native import numa_utils; print(numa_utils.get_max_node())"
```

## Troubleshooting

### Common Issues

**Permission Denied on Cgroups**
```bash
sudo chown root:root /sys/fs/cgroup
sudo chmod 755 /sys/fs/cgroup
```

**NUMA Extension Build Failure**
```bash
sudo apt-get install libnuma-dev  # Ubuntu/Debian
sudo yum install numactl-devel    # RHEL/CentOS
```

**NVIDIA-SMI Not Found**
```bash
export PATH=$PATH:/usr/local/cuda/bin
# Or install nvidia-utils package
```

**MIG Not Supported**
- Ensure GPU supports MIG (A100, H100)
- Enable MIG mode: `sudo nvidia-smi -mig 1`
- Check driver version compatibility

### Debug Mode

Enable debug logging for detailed troubleshooting:

```bash
sudo gpu-controller --debug
```

This provides verbose logging of:
- Topology discovery process
- Metric collection details  
- State transitions
- Action execution steps

## Contributing

We welcome contributions! Please see our [Contributing Guidelines](CONTRIBUTING.md) for details.

### Development Workflow

1. Fork the repository
2. Create a feature branch
3. Make your changes with tests
4. Run the test suite
5. Submit a pull request

### Reporting Issues

Please use the [GitHub issue tracker](https://github.com/riteshr19/Cluster-Helper/issues) to report bugs or request features.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- NVIDIA for GPU management tools and documentation
- Linux NUMA community for libnuma
- hwloc project for topology discovery
- Open source community for inspiration and best practices

---

**⚠️ Note**: Cluster-Helper requires root privileges and can modify system resources. Always test in a non-production environment first.