"""
Configuration management for cluster-helper.

This module provides typed configuration parsing from /etc/gpu-controller.conf
with validation for all controller and placement parameters.
"""

import configparser
import logging
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


class ControllerConfig:
    """Configuration manager for cluster-helper daemon."""
    
    DEFAULT_CONFIG_PATH = "/etc/gpu-controller.conf"
    
    def __init__(self, config_path: Optional[str] = None):
        """Initialize configuration from file.
        
        Args:
            config_path: Path to configuration file. Defaults to /etc/gpu-controller.conf
        """
        self.config_path = config_path or self.DEFAULT_CONFIG_PATH
        self._config = configparser.ConfigParser()
        self._load_config()
        self._validate_config()
    
    def _load_config(self) -> None:
        """Load configuration from file."""
        config_file = Path(self.config_path)
        if not config_file.exists():
            logger.warning(f"Config file {self.config_path} not found, using defaults")
            self._set_defaults()
            return
        
        try:
            self._config.read(self.config_path)
            logger.info(f"Loaded configuration from {self.config_path}")
        except Exception as e:
            logger.error(f"Failed to load config from {self.config_path}: {e}")
            self._set_defaults()
    
    def _set_defaults(self) -> None:
        """Set default configuration values."""
        self._config.add_section('controller')
        self._config.set('controller', 'tail_threshold_ms', '100.0')
        self._config.set('controller', 'persistence_windows', '3')
        self._config.set('controller', 'cooldown_observations', '10')
        self._config.set('controller', 'poll_interval_sec', '30.0')
        self._config.set('controller', 'log_level', 'INFO')
        
        self._config.add_section('placement')
        self._config.set('placement', 'numa_weight', '2.0')
        self._config.set('placement', 'pcie_weight', '1.5')
        self._config.set('placement', 'enable_mig_reconfiguration', 'true')
        self._config.set('placement', 'max_cgroup_io_limit_mbps', '1000')
    
    def _validate_config(self) -> None:
        """Validate configuration values are within sensible ranges."""
        # Controller section validation
        if self.tail_threshold_ms <= 0:
            raise ValueError(f"tail_threshold_ms must be positive, got {self.tail_threshold_ms}")
        
        if self.persistence_windows < 1:
            raise ValueError(f"persistence_windows must be >= 1, got {self.persistence_windows}")
        
        if self.cooldown_observations < 1:
            raise ValueError(f"cooldown_observations must be >= 1, got {self.cooldown_observations}")
        
        if self.poll_interval_sec <= 0:
            raise ValueError(f"poll_interval_sec must be positive, got {self.poll_interval_sec}")
        
        # Placement section validation
        if self.numa_weight < 0:
            raise ValueError(f"numa_weight must be non-negative, got {self.numa_weight}")
        
        if self.pcie_weight < 0:
            raise ValueError(f"pcie_weight must be non-negative, got {self.pcie_weight}")
        
        if self.max_cgroup_io_limit_mbps <= 0:
            raise ValueError(f"max_cgroup_io_limit_mbps must be positive, got {self.max_cgroup_io_limit_mbps}")
    
    # Controller section properties
    @property
    def tail_threshold_ms(self) -> float:
        """SLO threshold in milliseconds for p99 latency."""
        return self._config.getfloat('controller', 'tail_threshold_ms')
    
    @property
    def persistence_windows(self) -> int:
        """Number of consecutive violations required before taking action."""
        return self._config.getint('controller', 'persistence_windows')
    
    @property
    def cooldown_observations(self) -> int:
        """Number of observation cycles to wait before re-evaluating same tenant."""
        return self._config.getint('controller', 'cooldown_observations')
    
    @property
    def poll_interval_sec(self) -> float:
        """Interval between monitoring cycles in seconds."""
        return self._config.getfloat('controller', 'poll_interval_sec')
    
    @property
    def log_level(self) -> str:
        """Logging level for the daemon."""
        return self._config.get('controller', 'log_level')
    
    # Placement section properties
    @property
    def numa_weight(self) -> float:
        """Weight applied to NUMA affinity penalties."""
        return self._config.getfloat('placement', 'numa_weight')
    
    @property
    def pcie_weight(self) -> float:
        """Weight applied to PCIe topology penalties."""
        return self._config.getfloat('placement', 'pcie_weight')
    
    @property
    def enable_mig_reconfiguration(self) -> bool:
        """Whether MIG reconfiguration is enabled."""
        return self._config.getboolean('placement', 'enable_mig_reconfiguration')
    
    @property
    def max_cgroup_io_limit_mbps(self) -> int:
        """Maximum I/O limit to apply via cgroups in MB/s."""
        return self._config.getint('placement', 'max_cgroup_io_limit_mbps')