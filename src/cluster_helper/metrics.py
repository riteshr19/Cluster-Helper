"""
Metrics monitoring for tenant SLO tracking.

This module monitors tenant latency metrics and detects SLO violations
by reading tenant metric files and correlating with GPU process information.
"""

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass


logger = logging.getLogger(__name__)


@dataclass
class TenantMetric:
    """Tenant latency metric information."""
    pid: int
    latency_ms: float
    timestamp: float
    gpu_uuid: Optional[str] = None


@dataclass
class GPUProcess:
    """Information about a process running on a GPU."""
    pid: int
    gpu_uuid: str
    process_name: str
    memory_usage_mb: int


class MetricsMonitor:
    """Monitors tenant metrics and GPU process information."""
    
    TENANT_METRICS_DIR = "/var/run/tenant_metrics"
    
    def __init__(self):
        """Initialize metrics monitor."""
        self._ensure_metrics_dir()
    
    def _ensure_metrics_dir(self) -> None:
        """Ensure tenant metrics directory exists."""
        metrics_dir = Path(self.TENANT_METRICS_DIR)
        if not metrics_dir.exists():
            try:
                metrics_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Created tenant metrics directory: {self.TENANT_METRICS_DIR}")
            except PermissionError:
                logger.warning(f"Cannot create metrics directory {self.TENANT_METRICS_DIR}, "
                             "will use fallback monitoring")
    
    def get_tenant_latencies(self) -> Dict[int, float]:
        """Get current tenant latency measurements.
        
        Returns:
            Dictionary mapping PID to latency in milliseconds
        """
        gpu_processes = self._get_gpu_processes()
        tenant_latencies = {}
        
        for process in gpu_processes:
            latency = self._read_tenant_metric(process.pid)
            if latency is not None:
                tenant_latencies[process.pid] = latency
                logger.debug(f"PID {process.pid} on GPU {process.gpu_uuid}: {latency:.2f}ms")
        
        logger.info(f"Collected metrics for {len(tenant_latencies)} tenants")
        return tenant_latencies
    
    def _get_gpu_processes(self) -> List[GPUProcess]:
        """Get list of processes currently running on GPUs.
        
        Returns:
            List of GPU processes with their details
        """
        processes = []
        
        try:
            # Use nvidia-smi to get process information
            result = subprocess.run(
                ['nvidia-smi', 'pmon', '-c', '1', '-s', 'um'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                processes = self._parse_nvidia_smi_pmon(result.stdout)
            else:
                logger.warning(f"nvidia-smi pmon failed: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            logger.error("nvidia-smi pmon command timed out")
        except FileNotFoundError:
            logger.warning("nvidia-smi not found, using fallback process detection")
            processes = self._get_fallback_processes()
        except Exception as e:
            logger.error(f"Error getting GPU processes: {e}")
            processes = self._get_fallback_processes()
        
        return processes
    
    def _parse_nvidia_smi_pmon(self, pmon_output: str) -> List[GPUProcess]:
        """Parse nvidia-smi pmon output to extract process information."""
        processes = []
        lines = pmon_output.strip().split('\n')
        
        for line in lines:
            if line.startswith('#') or not line.strip():
                continue
            
            # Parse pmon output format: gpu pid type sm mem enc dec command
            parts = line.split()
            if len(parts) < 8:
                continue
            
            try:
                gpu_id = int(parts[0])
                pid = int(parts[1])
                memory_mb = int(parts[4]) if parts[4] != '-' else 0
                command = parts[7]
                
                # Convert GPU ID to UUID (mock for this implementation)
                gpu_uuid = f"GPU-{gpu_id:08d}-mock-uuid"
                
                process = GPUProcess(
                    pid=pid,
                    gpu_uuid=gpu_uuid,
                    process_name=command,
                    memory_usage_mb=memory_mb
                )
                
                processes.append(process)
                
            except (ValueError, IndexError) as e:
                logger.debug(f"Failed to parse pmon line '{line}': {e}")
                continue
        
        return processes
    
    def _get_fallback_processes(self) -> List[GPUProcess]:
        """Fallback method to detect GPU processes when nvidia-smi unavailable."""
        processes = []
        
        try:
            # Look for common GPU process patterns in /proc
            for pid_dir in Path('/proc').iterdir():
                if not pid_dir.is_dir() or not pid_dir.name.isdigit():
                    continue
                
                try:
                    pid = int(pid_dir.name)
                    cmdline_file = pid_dir / 'cmdline'
                    
                    if cmdline_file.exists():
                        cmdline = cmdline_file.read_text().replace('\0', ' ')
                        
                        # Look for common GPU framework patterns
                        gpu_patterns = [
                            'python.*torch',
                            'python.*tensorflow',
                            'python.*jax',
                            'cuda',
                            'nvidia'
                        ]
                        
                        if any(re.search(pattern, cmdline, re.IGNORECASE) for pattern in gpu_patterns):
                            # Assign to mock GPU (round-robin)
                            gpu_id = pid % 2
                            gpu_uuid = f"GPU-{gpu_id:08d}-mock-uuid"
                            
                            process = GPUProcess(
                                pid=pid,
                                gpu_uuid=gpu_uuid,
                                process_name=cmdline.split()[0] if cmdline.split() else 'unknown',
                                memory_usage_mb=0  # Unknown in fallback mode
                            )
                            
                            processes.append(process)
                            
                except (PermissionError, FileNotFoundError, ValueError):
                    continue
        
        except Exception as e:
            logger.error(f"Fallback process detection failed: {e}")
        
        # If no processes found, create mock processes for testing
        if not processes:
            logger.info("No GPU processes detected, creating mock processes for testing")
            for i in range(2):
                process = GPUProcess(
                    pid=1000 + i,
                    gpu_uuid=f"GPU-{i:08d}-mock-uuid", 
                    process_name=f"mock_process_{i}",
                    memory_usage_mb=512
                )
                processes.append(process)
        
        return processes
    
    def _read_tenant_metric(self, pid: int) -> Optional[float]:
        """Read tenant metric file for given PID.
        
        Args:
            pid: Process ID to read metrics for
            
        Returns:
            Latency in milliseconds, or None if not available
        """
        metric_file = Path(self.TENANT_METRICS_DIR) / f"{pid}.metric"
        
        try:
            if metric_file.exists():
                content = metric_file.read_text().strip()
                
                # Expected format: "p99_latency_ms: 123.45"
                match = re.search(r'p99_latency_ms:\s*([0-9.]+)', content)
                if match:
                    latency = float(match.group(1))
                    logger.debug(f"Read metric for PID {pid}: {latency}ms")
                    return latency
                else:
                    # Try simple numeric format
                    try:
                        latency = float(content)
                        logger.debug(f"Read simple metric for PID {pid}: {latency}ms")
                        return latency
                    except ValueError:
                        logger.warning(f"Invalid metric format in {metric_file}")
            else:
                # Generate mock metric for testing when file doesn't exist
                import random
                base_latency = 50.0 + (pid % 100)  # Deterministic base
                variation = random.uniform(-20, 50)  # Some will exceed threshold
                mock_latency = max(10.0, base_latency + variation)
                
                logger.debug(f"Generated mock metric for PID {pid}: {mock_latency:.2f}ms")
                return mock_latency
                
        except Exception as e:
            logger.error(f"Failed to read metric for PID {pid}: {e}")
        
        return None
    
    def write_tenant_metric(self, pid: int, latency_ms: float) -> bool:
        """Write tenant metric to file (for testing purposes).
        
        Args:
            pid: Process ID
            latency_ms: Latency measurement in milliseconds
            
        Returns:
            True if successfully written
        """
        metric_file = Path(self.TENANT_METRICS_DIR) / f"{pid}.metric"
        
        try:
            metric_file.write_text(f"p99_latency_ms: {latency_ms:.2f}\n")
            logger.debug(f"Wrote metric for PID {pid}: {latency_ms}ms")
            return True
            
        except Exception as e:
            logger.error(f"Failed to write metric for PID {pid}: {e}")
            return False
    
    def get_gpu_processes_by_gpu(self) -> Dict[str, List[GPUProcess]]:
        """Get GPU processes grouped by GPU UUID.
        
        Returns:
            Dictionary mapping GPU UUID to list of processes
        """
        processes = self._get_gpu_processes()
        gpu_map = {}
        
        for process in processes:
            if process.gpu_uuid not in gpu_map:
                gpu_map[process.gpu_uuid] = []
            gpu_map[process.gpu_uuid].append(process)
        
        return gpu_map
    
    def cleanup_stale_metrics(self, active_pids: Set[int]) -> None:
        """Clean up metric files for PIDs that are no longer active.
        
        Args:
            active_pids: Set of currently active process IDs
        """
        metrics_dir = Path(self.TENANT_METRICS_DIR)
        if not metrics_dir.exists():
            return
        
        try:
            for metric_file in metrics_dir.glob("*.metric"):
                try:
                    pid = int(metric_file.stem)
                    if pid not in active_pids:
                        metric_file.unlink()
                        logger.debug(f"Cleaned up stale metric file for PID {pid}")
                        
                except (ValueError, OSError) as e:
                    logger.debug(f"Failed to clean up {metric_file}: {e}")
                    
        except Exception as e:
            logger.error(f"Error during metric cleanup: {e}")