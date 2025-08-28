"""
State management for tracking tenant SLO violations and cooldowns.

This module manages the state machine for tracking persistent violations
and implementing cooldown periods to prevent excessive action taking.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from enum import Enum


logger = logging.getLogger(__name__)


class ViolationState(Enum):
    """States for tracking tenant violations."""
    NORMAL = "normal"
    DEGRADED = "degraded"
    VIOLATED = "violated"
    COOLDOWN = "cooldown"


@dataclass
class TenantState:
    """Tracks the state and history of a single tenant."""
    
    pid: int
    gpu_uuid: Optional[str] = None
    state: ViolationState = ViolationState.NORMAL
    latency_history: deque = field(default_factory=lambda: deque(maxlen=10))
    violation_count: int = 0
    last_action_time: float = 0.0
    cooldown_remaining: int = 0
    
    def add_latency_measurement(self, latency_ms: float, timestamp: float = None) -> None:
        """Add a new latency measurement to the history.
        
        Args:
            latency_ms: Latency measurement in milliseconds
            timestamp: Measurement timestamp (defaults to current time)
        """
        if timestamp is None:
            timestamp = time.time()
            
        self.latency_history.append((timestamp, latency_ms))
    
    def get_recent_latencies(self, count: int = 5) -> List[float]:
        """Get the most recent latency measurements.
        
        Args:
            count: Number of recent measurements to return
            
        Returns:
            List of recent latency values in milliseconds
        """
        recent = list(self.latency_history)[-count:]
        return [latency for _, latency in recent]
    
    def is_in_cooldown(self) -> bool:
        """Check if tenant is currently in cooldown period."""
        return self.state == ViolationState.COOLDOWN and self.cooldown_remaining > 0
    
    def decrement_cooldown(self) -> None:
        """Decrement cooldown counter and update state if needed."""
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1
            
        if self.cooldown_remaining <= 0 and self.state == ViolationState.COOLDOWN:
            self.state = ViolationState.NORMAL
            logger.debug(f"PID {self.pid} cooldown period ended")


@dataclass
class Violation:
    """Represents a detected SLO violation requiring action."""
    
    victim_pid: int
    victim_gpu: str
    bully_pids: List[int]
    violation_severity: float  # How much the SLO is exceeded
    timestamp: float = field(default_factory=time.time)
    
    def __str__(self) -> str:
        return (f"Violation(victim={self.victim_pid}, gpu={self.victim_gpu}, "
                f"bullies={self.bully_pids}, severity={self.violation_severity:.2f})")


class StateManager:
    """Manages state for all tenants and detects violations."""
    
    def __init__(self, tail_threshold_ms: float = 100.0, 
                 persistence_windows: int = 3, 
                 cooldown_observations: int = 10):
        """Initialize state manager.
        
        Args:
            tail_threshold_ms: SLO threshold for p99 latency
            persistence_windows: Consecutive violations required for action
            cooldown_observations: Cooldown period in observation cycles
        """
        self.tail_threshold_ms = tail_threshold_ms
        self.persistence_windows = persistence_windows
        self.cooldown_observations = cooldown_observations
        
        self._tenant_states: Dict[int, TenantState] = {}
        self._gpu_process_mapping: Dict[str, Set[int]] = {}
        
        logger.info(f"StateManager initialized: threshold={tail_threshold_ms}ms, "
                   f"persistence={persistence_windows}, cooldown={cooldown_observations}")
    
    def update(self, latest_metrics: Dict[int, float]) -> List[Violation]:
        """Update tenant states with latest metrics and detect violations.
        
        Args:
            latest_metrics: Dictionary mapping PID to latency in milliseconds
            
        Returns:
            List of violations requiring immediate action
        """
        timestamp = time.time()
        violations = []
        
        # Update all tenant states with new measurements
        for pid, latency_ms in latest_metrics.items():
            self._update_tenant_state(pid, latency_ms, timestamp)
        
        # Process cooldowns for all tenants
        self._process_cooldowns()
        
        # Clean up stale tenant states
        self._cleanup_stale_tenants(set(latest_metrics.keys()))
        
        # Detect new violations
        violations = self._detect_violations()
        
        if violations:
            logger.warning(f"Detected {len(violations)} SLO violations")
            for violation in violations:
                logger.warning(f"  {violation}")
        
        return violations
    
    def _update_tenant_state(self, pid: int, latency_ms: float, timestamp: float) -> None:
        """Update state for a specific tenant."""
        if pid not in self._tenant_states:
            self._tenant_states[pid] = TenantState(pid=pid)
            logger.debug(f"Created new tenant state for PID {pid}")
        
        tenant = self._tenant_states[pid]
        tenant.add_latency_measurement(latency_ms, timestamp)
        
        # Update state based on current latency
        if latency_ms > self.tail_threshold_ms:
            if tenant.state == ViolationState.NORMAL:
                tenant.state = ViolationState.DEGRADED
                tenant.violation_count = 1
                logger.debug(f"PID {pid} entered degraded state (latency: {latency_ms:.2f}ms)")
            elif tenant.state == ViolationState.DEGRADED:
                tenant.violation_count += 1
                if tenant.violation_count >= self.persistence_windows:
                    tenant.state = ViolationState.VIOLATED
                    logger.info(f"PID {pid} entered violated state after "
                              f"{tenant.violation_count} consecutive violations")
        else:
            # Latency is within SLO
            if tenant.state in [ViolationState.DEGRADED, ViolationState.VIOLATED]:
                tenant.state = ViolationState.NORMAL
                tenant.violation_count = 0
                logger.debug(f"PID {pid} returned to normal state (latency: {latency_ms:.2f}ms)")
    
    def _process_cooldowns(self) -> None:
        """Process cooldown periods for all tenants."""
        for tenant in self._tenant_states.values():
            if tenant.is_in_cooldown():
                tenant.decrement_cooldown()
    
    def _cleanup_stale_tenants(self, active_pids: Set[int]) -> None:
        """Remove tenant states for PIDs that are no longer active."""
        stale_pids = set(self._tenant_states.keys()) - active_pids
        
        for pid in stale_pids:
            del self._tenant_states[pid]
            logger.debug(f"Cleaned up state for inactive PID {pid}")
        
        if stale_pids:
            logger.info(f"Cleaned up {len(stale_pids)} stale tenant states")
    
    def _detect_violations(self) -> List[Violation]:
        """Detect violations that require immediate action."""
        violations = []
        
        # Group tenants by GPU for violation detection
        gpu_tenants = self._group_tenants_by_gpu()
        
        for gpu_uuid, tenant_pids in gpu_tenants.items():
            gpu_violations = self._detect_gpu_violations(gpu_uuid, tenant_pids)
            violations.extend(gpu_violations)
        
        return violations
    
    def _group_tenants_by_gpu(self) -> Dict[str, List[int]]:
        """Group active tenants by their GPU assignments."""
        gpu_groups = {}
        
        for pid, tenant in self._tenant_states.items():
            # For mock implementation, assign GPUs based on PID
            gpu_uuid = tenant.gpu_uuid or f"GPU-{pid % 2:08d}-mock-uuid"
            tenant.gpu_uuid = gpu_uuid  # Update if not set
            
            if gpu_uuid not in gpu_groups:
                gpu_groups[gpu_uuid] = []
            gpu_groups[gpu_uuid].append(pid)
        
        return gpu_groups
    
    def _detect_gpu_violations(self, gpu_uuid: str, tenant_pids: List[int]) -> List[Violation]:
        """Detect violations on a specific GPU."""
        violations = []
        
        # Find tenants in violated state that are not in cooldown
        violated_tenants = []
        potential_bullies = []
        
        for pid in tenant_pids:
            tenant = self._tenant_states[pid]
            
            if tenant.state == ViolationState.VIOLATED and not tenant.is_in_cooldown():
                violated_tenants.append(pid)
            elif tenant.state != ViolationState.VIOLATED:
                # Potential bully - any tenant that's not also violated
                potential_bullies.append(pid)
        
        # Create violations for each violated tenant
        for victim_pid in violated_tenants:
            victim_tenant = self._tenant_states[victim_pid]
            recent_latencies = victim_tenant.get_recent_latencies(3)
            
            if recent_latencies:
                avg_latency = sum(recent_latencies) / len(recent_latencies)
                severity = (avg_latency - self.tail_threshold_ms) / self.tail_threshold_ms
                
                violation = Violation(
                    victim_pid=victim_pid,
                    victim_gpu=gpu_uuid,
                    bully_pids=potential_bullies.copy(),
                    violation_severity=severity
                )
                
                violations.append(violation)
                
                # Put victim in cooldown to prevent repeated actions
                victim_tenant.state = ViolationState.COOLDOWN
                victim_tenant.cooldown_remaining = self.cooldown_observations
                victim_tenant.last_action_time = time.time()
                
                logger.info(f"Created violation for PID {victim_pid} on GPU {gpu_uuid}, "
                           f"severity: {severity:.2f}")
        
        return violations
    
    def get_tenant_state(self, pid: int) -> Optional[TenantState]:
        """Get current state for a specific tenant."""
        return self._tenant_states.get(pid)
    
    def get_all_tenant_states(self) -> Dict[int, TenantState]:
        """Get all current tenant states."""
        return self._tenant_states.copy()
    
    def get_violation_summary(self) -> Dict[str, int]:
        """Get summary of current tenant states."""
        summary = {state.value: 0 for state in ViolationState}
        
        for tenant in self._tenant_states.values():
            summary[tenant.state.value] += 1
        
        return summary
    
    def force_cooldown(self, pid: int, duration: Optional[int] = None) -> bool:
        """Force a tenant into cooldown state.
        
        Args:
            pid: Process ID to put in cooldown
            duration: Cooldown duration (defaults to configured value)
            
        Returns:
            True if successful
        """
        if pid not in self._tenant_states:
            return False
        
        tenant = self._tenant_states[pid]
        tenant.state = ViolationState.COOLDOWN
        tenant.cooldown_remaining = duration or self.cooldown_observations
        tenant.last_action_time = time.time()
        
        logger.info(f"Forced PID {pid} into cooldown for {tenant.cooldown_remaining} cycles")
        return True