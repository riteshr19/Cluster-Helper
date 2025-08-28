"""
Action execution for SLO violation mitigation.

This module implements the actual mitigation actions including cgroup limits,
MIG reconfiguration, and tiered response strategies.
"""

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from .state import Violation


logger = logging.getLogger(__name__)


class ActionType(Enum):
    """Types of mitigation actions."""
    CGROUP_IO_LIMIT = "cgroup_io_limit"
    MIG_RECONFIGURE = "mig_reconfigure"
    PROCESS_PRIORITY = "process_priority"


@dataclass
class ActionResult:
    """Result of an executed action."""
    action_type: ActionType
    success: bool
    message: str
    timestamp: float
    target_pid: Optional[int] = None
    target_gpu: Optional[str] = None


class ActionExecutor:
    """Executes mitigation actions for SLO violations."""
    
    def __init__(self, max_io_limit_mbps: int = 1000, 
                 enable_mig: bool = True):
        """Initialize action executor.
        
        Args:
            max_io_limit_mbps: Maximum I/O limit to apply via cgroups
            enable_mig: Whether MIG reconfiguration is enabled
        """
        self.max_io_limit_mbps = max_io_limit_mbps
        self.enable_mig = enable_mig
        self._action_history: List[ActionResult] = []
        
        logger.info(f"ActionExecutor initialized: max_io_limit={max_io_limit_mbps}MB/s, "
                   f"mig_enabled={enable_mig}")
    
    def mitigate_violation(self, violation: Violation) -> List[ActionResult]:
        """Execute tiered response to mitigate an SLO violation.
        
        Args:
            violation: The violation to mitigate
            
        Returns:
            List of action results from the mitigation attempt
        """
        logger.info(f"Mitigating violation: {violation}")
        results = []
        
        # Tier 1: Apply cgroup I/O limits to bully processes
        if violation.bully_pids:
            logger.info(f"Tier 1: Applying cgroup limits to {len(violation.bully_pids)} bullies")
            
            for bully_pid in violation.bully_pids:
                # Calculate I/O limit based on violation severity
                io_limit = self._calculate_io_limit(violation.violation_severity)
                result = self.apply_cgroup_io_limit(bully_pid, io_limit)
                results.append(result)
                
                if not result.success:
                    logger.warning(f"Failed to apply cgroup limit to PID {bully_pid}: {result.message}")
        
        # Tier 2: MIG reconfiguration if enabled and Tier 1 insufficient
        if self.enable_mig and violation.violation_severity > 0.5:
            logger.info(f"Tier 2: Attempting MIG reconfiguration for GPU {violation.victim_gpu}")
            
            mig_result = self.reconfigure_mig_profile(
                violation.victim_gpu, 
                self._select_mig_profile(violation)
            )
            results.append(mig_result)
        
        # Record all actions
        self._action_history.extend(results)
        
        # Log summary
        successful_actions = sum(1 for r in results if r.success)
        logger.info(f"Mitigation complete: {successful_actions}/{len(results)} actions successful")
        
        return results
    
    def apply_cgroup_io_limit(self, pid: int, limit_bytes_per_sec: int) -> ActionResult:
        """Apply cgroup I/O limit to a process.
        
        Args:
            pid: Process ID to limit
            limit_bytes_per_sec: I/O limit in bytes per second
            
        Returns:
            Result of the action attempt
        """
        try:
            # Find the cgroup path for the process
            cgroup_path = self._find_process_cgroup(pid)
            if not cgroup_path:
                return ActionResult(
                    action_type=ActionType.CGROUP_IO_LIMIT,
                    success=False,
                    message=f"Could not find cgroup path for PID {pid}",
                    timestamp=time.time(),
                    target_pid=pid
                )
            
            # Apply I/O limit
            success, message = self._write_cgroup_io_limit(cgroup_path, limit_bytes_per_sec)
            
            if success:
                logger.info(f"Applied I/O limit {limit_bytes_per_sec} bytes/s to PID {pid}")
            
            return ActionResult(
                action_type=ActionType.CGROUP_IO_LIMIT,
                success=success,
                message=message,
                timestamp=time.time(),
                target_pid=pid
            )
            
        except Exception as e:
            error_msg = f"Exception applying cgroup limit to PID {pid}: {e}"
            logger.error(error_msg)
            
            return ActionResult(
                action_type=ActionType.CGROUP_IO_LIMIT,
                success=False,
                message=error_msg,
                timestamp=time.time(),
                target_pid=pid
            )
    
    def _find_process_cgroup(self, pid: int) -> Optional[str]:
        """Find the cgroup path for a given process ID."""
        try:
            # Read /proc/PID/cgroup to find cgroup membership
            cgroup_file = Path(f"/proc/{pid}/cgroup")
            if not cgroup_file.exists():
                logger.debug(f"Process {pid} not found")
                return None
            
            cgroup_content = cgroup_file.read_text()
            
            # Look for the cgroup v2 unified hierarchy (hierarchy ID 0)
            for line in cgroup_content.split('\n'):
                if line.startswith('0::'):
                    cgroup_path = line.split('::', 1)[1]
                    # Convert to filesystem path
                    full_path = f"/sys/fs/cgroup{cgroup_path}"
                    
                    if Path(full_path).exists():
                        return full_path
            
            # Fallback: try to construct path from UID
            try:
                import pwd
                stat_info = os.stat(f"/proc/{pid}")
                uid = stat_info.st_uid
                user_info = pwd.getpwuid(uid)
                username = user_info.pw_name
                
                # Common cgroup paths
                possible_paths = [
                    f"/sys/fs/cgroup/system.slice/user-{uid}.slice",
                    f"/sys/fs/cgroup/user.slice/user-{uid}.slice",
                    f"/sys/fs/cgroup/system.slice/user@{uid}.service"
                ]
                
                for path in possible_paths:
                    if Path(path).exists():
                        return path
                        
            except (KeyError, FileNotFoundError, PermissionError):
                pass
            
            logger.debug(f"Could not determine cgroup path for PID {pid}")
            return None
            
        except Exception as e:
            logger.debug(f"Error finding cgroup for PID {pid}: {e}")
            return None
    
    def _write_cgroup_io_limit(self, cgroup_path: str, limit_bytes_per_sec: int) -> Tuple[bool, str]:
        """Write I/O limit to cgroup control file."""
        try:
            # Find available block devices
            devices = self._get_block_devices()
            if not devices:
                return False, "No block devices found for I/O limiting"
            
            io_max_file = Path(cgroup_path) / "io.max"
            
            # Format: "major:minor rbps wbps riops wiops"
            # We'll limit both read and write bandwidth
            limit_lines = []
            for device in devices:
                # Apply limit to both read and write
                limit_line = f"{device} rbps={limit_bytes_per_sec} wbps={limit_bytes_per_sec}"
                limit_lines.append(limit_line)
            
            # Write limits
            limit_content = '\n'.join(limit_lines) + '\n'
            
            # Check if we can write (may require root privileges)
            if not os.access(cgroup_path, os.W_OK):
                return False, f"No write permission to {cgroup_path}"
            
            io_max_file.write_text(limit_content)
            
            return True, f"Applied I/O limit to {len(devices)} devices"
            
        except PermissionError:
            return False, f"Permission denied writing to {cgroup_path}/io.max"
        except Exception as e:
            return False, f"Error writing I/O limit: {e}"
    
    def _get_block_devices(self) -> List[str]:
        """Get list of block devices for I/O limiting."""
        devices = []
        
        try:
            # Read /proc/partitions to find block devices
            partitions_file = Path("/proc/partitions")
            if partitions_file.exists():
                content = partitions_file.read_text()
                
                for line in content.split('\n')[2:]:  # Skip header
                    if not line.strip():
                        continue
                    
                    parts = line.split()
                    if len(parts) >= 4:
                        major = parts[0]
                        minor = parts[1]
                        device_name = parts[3]
                        
                        # Focus on main disk devices (not partitions)
                        if device_name.startswith(('sd', 'nvme', 'hd')) and \
                           not any(c.isdigit() for c in device_name[-2:]):
                            devices.append(f"{major}:{minor}")
            
            # If no devices found, use common defaults
            if not devices:
                devices = ["8:0", "259:0"]  # Common for sda and nvme0n1
                
        except Exception as e:
            logger.debug(f"Error getting block devices: {e}")
            devices = ["8:0"]  # Fallback
        
        return devices
    
    def reconfigure_mig_profile(self, gpu_uuid: str, profile_spec: str) -> ActionResult:
        """Reconfigure MIG profile for a GPU.
        
        Args:
            gpu_uuid: GPU UUID to reconfigure
            profile_spec: MIG profile specification (e.g., "1g.5gb:4")
            
        Returns:
            Result of the reconfiguration attempt
        """
        try:
            if not self.enable_mig:
                return ActionResult(
                    action_type=ActionType.MIG_RECONFIGURE,
                    success=False,
                    message="MIG reconfiguration is disabled",
                    timestamp=time.time(),
                    target_gpu=gpu_uuid
                )
            
            # Convert UUID to GPU index for nvidia-smi (mock conversion)
            gpu_index = self._gpu_uuid_to_index(gpu_uuid)
            
            logger.info(f"Reconfiguring MIG profile for GPU {gpu_index} to {profile_spec}")
            
            # Step 1: Disable MIG mode if enabled
            disable_cmd = ['nvidia-smi', '-i', str(gpu_index), '-mig', '0']
            result = subprocess.run(
                disable_cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                logger.warning(f"Failed to disable MIG: {result.stderr}")
                # Continue anyway - might already be disabled
            
            # Step 2: Enable MIG mode
            enable_cmd = ['nvidia-smi', '-i', str(gpu_index), '-mig', '1']
            result = subprocess.run(
                enable_cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                error_msg = f"Failed to enable MIG: {result.stderr}"
                logger.error(error_msg)
                return ActionResult(
                    action_type=ActionType.MIG_RECONFIGURE,
                    success=False,
                    message=error_msg,
                    timestamp=time.time(),
                    target_gpu=gpu_uuid
                )
            
            # Step 3: Configure MIG instances
            config_cmd = ['nvidia-smi', 'mig', '-i', str(gpu_index), '-cgi', profile_spec]
            result = subprocess.run(
                config_cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                success_msg = f"Successfully configured MIG profile {profile_spec}"
                logger.info(success_msg)
                
                return ActionResult(
                    action_type=ActionType.MIG_RECONFIGURE,
                    success=True,
                    message=success_msg,
                    timestamp=time.time(),
                    target_gpu=gpu_uuid
                )
            else:
                error_msg = f"Failed to configure MIG profile: {result.stderr}"
                logger.error(error_msg)
                
                return ActionResult(
                    action_type=ActionType.MIG_RECONFIGURE,
                    success=False,
                    message=error_msg,
                    timestamp=time.time(),
                    target_gpu=gpu_uuid
                )
                
        except subprocess.TimeoutExpired:
            error_msg = f"MIG reconfiguration timed out for GPU {gpu_uuid}"
            logger.error(error_msg)
            
            return ActionResult(
                action_type=ActionType.MIG_RECONFIGURE,
                success=False,
                message=error_msg,
                timestamp=time.time(),
                target_gpu=gpu_uuid
            )
            
        except FileNotFoundError:
            error_msg = "nvidia-smi not found - cannot perform MIG reconfiguration"
            logger.error(error_msg)
            
            return ActionResult(
                action_type=ActionType.MIG_RECONFIGURE,
                success=False,
                message=error_msg,
                timestamp=time.time(),
                target_gpu=gpu_uuid
            )
            
        except Exception as e:
            error_msg = f"Exception during MIG reconfiguration: {e}"
            logger.error(error_msg)
            
            return ActionResult(
                action_type=ActionType.MIG_RECONFIGURE,
                success=False,
                message=error_msg,
                timestamp=time.time(),
                target_gpu=gpu_uuid
            )
    
    def _gpu_uuid_to_index(self, gpu_uuid: str) -> int:
        """Convert GPU UUID to index for nvidia-smi commands."""
        # Mock implementation - extract index from mock UUID
        if "mock-uuid" in gpu_uuid:
            try:
                return int(gpu_uuid.split('-')[1])
            except (IndexError, ValueError):
                return 0
        
        # For real UUIDs, would need to query nvidia-smi
        return 0
    
    def _calculate_io_limit(self, violation_severity: float) -> int:
        """Calculate appropriate I/O limit based on violation severity."""
        # Base limit is 50% of max, reduced further based on severity
        base_limit_mbps = self.max_io_limit_mbps * 0.5
        severity_factor = max(0.1, 1.0 - violation_severity)
        
        limit_mbps = int(base_limit_mbps * severity_factor)
        limit_bytes_per_sec = limit_mbps * 1024 * 1024
        
        logger.debug(f"Calculated I/O limit: {limit_mbps}MB/s "
                    f"(severity: {violation_severity:.2f})")
        
        return limit_bytes_per_sec
    
    def _select_mig_profile(self, violation: Violation) -> str:
        """Select appropriate MIG profile based on violation characteristics."""
        # Simple policy: more severe violations get more aggressive isolation
        if violation.violation_severity > 1.0:
            # High severity: create smaller instances for better isolation
            return "1g.5gb:7"  # 7 small instances
        elif violation.violation_severity > 0.5:
            # Medium severity: balanced approach
            return "2g.10gb:3"  # 3 medium instances
        else:
            # Low severity: minimal change
            return "3g.20gb:2"  # 2 large instances
    
    def get_action_history(self, action_type: Optional[ActionType] = None, 
                          limit: int = 100) -> List[ActionResult]:
        """Get history of executed actions.
        
        Args:
            action_type: Filter by specific action type
            limit: Maximum number of results to return
            
        Returns:
            List of action results
        """
        history = self._action_history
        
        if action_type:
            history = [r for r in history if r.action_type == action_type]
        
        return history[-limit:]
    
    def get_action_stats(self) -> Dict[str, int]:
        """Get statistics about executed actions."""
        stats = {
            "total_actions": len(self._action_history),
            "successful_actions": sum(1 for r in self._action_history if r.success),
            "failed_actions": sum(1 for r in self._action_history if not r.success)
        }
        
        # Break down by action type
        for action_type in ActionType:
            type_actions = [r for r in self._action_history if r.action_type == action_type]
            stats[f"{action_type.value}_total"] = len(type_actions)
            stats[f"{action_type.value}_successful"] = sum(1 for r in type_actions if r.success)
        
        return stats