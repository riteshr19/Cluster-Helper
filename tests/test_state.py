"""
Tests for state management and violation tracking.
"""

import pytest
import time
from cluster_helper.state import StateManager, TenantState, Violation, ViolationState


class TestTenantState:
    """Test individual tenant state management."""
    
    def test_tenant_state_initialization(self):
        """Test tenant state initialization."""
        state = TenantState(pid=1234)
        
        assert state.pid == 1234
        assert state.state == ViolationState.NORMAL
        assert state.violation_count == 0
        assert state.cooldown_remaining == 0
        assert len(state.latency_history) == 0
    
    def test_latency_measurement_addition(self):
        """Test adding latency measurements."""
        state = TenantState(pid=1234)
        
        state.add_latency_measurement(50.0)
        assert len(state.latency_history) == 1
        
        # Add more measurements
        for i in range(15):  # Exceed maxlen of 10
            state.add_latency_measurement(50.0 + i)
        
        # Should only keep last 10
        assert len(state.latency_history) == 10
        
        recent = state.get_recent_latencies(5)
        assert len(recent) == 5
        assert all(isinstance(lat, float) for lat in recent)
    
    def test_cooldown_functionality(self):
        """Test cooldown state management."""
        state = TenantState(pid=1234)
        
        # Not in cooldown initially
        assert not state.is_in_cooldown()
        
        # Put in cooldown
        state.state = ViolationState.COOLDOWN
        state.cooldown_remaining = 3
        
        assert state.is_in_cooldown()
        
        # Decrement cooldown
        state.decrement_cooldown()
        assert state.cooldown_remaining == 2
        assert state.is_in_cooldown()
        
        # Finish cooldown
        state.decrement_cooldown()
        state.decrement_cooldown()
        
        assert state.cooldown_remaining == 0
        assert not state.is_in_cooldown()
        assert state.state == ViolationState.NORMAL


class TestStateManager:
    """Test state manager functionality."""
    
    def test_state_manager_initialization(self):
        """Test state manager initialization."""
        manager = StateManager(
            tail_threshold_ms=100.0,
            persistence_windows=3,
            cooldown_observations=5
        )
        
        assert manager.tail_threshold_ms == 100.0
        assert manager.persistence_windows == 3
        assert manager.cooldown_observations == 5
        assert len(manager._tenant_states) == 0
    
    def test_normal_latency_handling(self):
        """Test handling of normal latency measurements."""
        manager = StateManager(tail_threshold_ms=100.0)
        
        # Add measurements under threshold
        metrics = {1234: 50.0, 5678: 75.0}
        violations = manager.update(metrics)
        
        assert len(violations) == 0
        assert len(manager._tenant_states) == 2
        
        # Both tenants should be in normal state
        for pid in metrics.keys():
            tenant = manager.get_tenant_state(pid)
            assert tenant.state == ViolationState.NORMAL
            assert tenant.violation_count == 0
    
    def test_violation_detection(self):
        """Test detection of SLO violations."""
        manager = StateManager(
            tail_threshold_ms=100.0,
            persistence_windows=2
        )
        
        # First violation - should go to degraded
        metrics = {1234: 150.0}
        violations = manager.update(metrics)
        
        assert len(violations) == 0  # Not persistent yet
        tenant = manager.get_tenant_state(1234)
        assert tenant.state == ViolationState.DEGRADED
        assert tenant.violation_count == 1
        
        # Second violation - should trigger violation
        violations = manager.update(metrics)
        
        assert len(violations) == 1
        tenant = manager.get_tenant_state(1234)
        assert tenant.state == ViolationState.COOLDOWN  # Moved to cooldown after violation
    
    def test_violation_recovery(self):
        """Test recovery from violations when latency improves."""
        manager = StateManager(tail_threshold_ms=100.0)
        
        # Cause violation
        metrics = {1234: 150.0}
        manager.update(metrics)
        
        tenant = manager.get_tenant_state(1234)
        assert tenant.state == ViolationState.DEGRADED
        
        # Recover with good latency
        metrics = {1234: 50.0}
        manager.update(metrics)
        
        tenant = manager.get_tenant_state(1234)
        assert tenant.state == ViolationState.NORMAL
        assert tenant.violation_count == 0
    
    def test_cooldown_processing(self):
        """Test cooldown period processing."""
        manager = StateManager(
            tail_threshold_ms=100.0,
            persistence_windows=1,
            cooldown_observations=3
        )
        
        # Trigger violation to enter cooldown
        metrics = {1234: 150.0}
        violations = manager.update(metrics)
        
        assert len(violations) == 1
        tenant = manager.get_tenant_state(1234)
        assert tenant.state == ViolationState.COOLDOWN
        assert tenant.cooldown_remaining == 3
        
        # Process cooldown cycles
        for i in range(3):
            manager.update({})  # Empty metrics to just process cooldowns
            
        tenant = manager.get_tenant_state(1234)
        assert tenant.cooldown_remaining == 0
        assert tenant.state == ViolationState.NORMAL
    
    def test_multiple_tenants(self):
        """Test handling multiple tenants simultaneously."""
        manager = StateManager(
            tail_threshold_ms=100.0,
            persistence_windows=1
        )
        
        # Multiple tenants with different latencies
        metrics = {
            1234: 50.0,   # Normal
            5678: 150.0,  # Violating
            9012: 75.0,   # Normal
            3456: 200.0   # Violating
        }
        
        violations = manager.update(metrics)
        
        # Should detect 2 violations (5678 and 3456)
        assert len(violations) == 2
        
        violating_pids = {v.victim_pid for v in violations}
        assert 5678 in violating_pids
        assert 3456 in violating_pids
    
    def test_stale_tenant_cleanup(self):
        """Test cleanup of stale tenant states."""
        manager = StateManager()
        
        # Add some tenants
        metrics = {1234: 50.0, 5678: 75.0, 9012: 100.0}
        manager.update(metrics)
        
        assert len(manager._tenant_states) == 3
        
        # Update with only subset of tenants
        metrics = {1234: 60.0}
        manager.update(metrics)
        
        # Should only have the active tenant
        assert len(manager._tenant_states) == 1
        assert 1234 in manager._tenant_states
    
    def test_violation_summary(self):
        """Test violation state summary generation."""
        manager = StateManager(tail_threshold_ms=100.0)
        
        # Add tenants in different states
        manager.update({1234: 50.0, 5678: 150.0})  # One normal, one degraded
        
        summary = manager.get_violation_summary()
        
        assert 'normal' in summary
        assert 'degraded' in summary
        assert summary['normal'] >= 1
        assert summary['degraded'] >= 1
    
    def test_force_cooldown(self):
        """Test forcing a tenant into cooldown."""
        manager = StateManager(cooldown_observations=5)
        
        # Add a tenant
        manager.update({1234: 50.0})
        
        # Force into cooldown
        success = manager.force_cooldown(1234)
        assert success
        
        tenant = manager.get_tenant_state(1234)
        assert tenant.state == ViolationState.COOLDOWN
        assert tenant.cooldown_remaining == 5
        
        # Try with non-existent tenant
        success = manager.force_cooldown(9999)
        assert not success


class TestViolation:
    """Test violation object functionality."""
    
    def test_violation_creation(self):
        """Test violation object creation."""
        violation = Violation(
            victim_pid=1234,
            victim_gpu="GPU-00000001-mock-uuid",
            bully_pids=[5678, 9012],
            violation_severity=1.5
        )
        
        assert violation.victim_pid == 1234
        assert violation.victim_gpu == "GPU-00000001-mock-uuid"
        assert violation.bully_pids == [5678, 9012]
        assert violation.violation_severity == 1.5
        assert violation.timestamp > 0
        
        # Test string representation
        str_repr = str(violation)
        assert "1234" in str_repr
        assert "GPU-00000001-mock-uuid" in str_repr
    
    def test_violation_with_timestamp(self):
        """Test violation with specific timestamp."""
        custom_time = time.time() - 3600  # 1 hour ago
        
        violation = Violation(
            victim_pid=1234,
            victim_gpu="GPU-00000001-mock-uuid",
            bully_pids=[],
            violation_severity=0.8,
            timestamp=custom_time
        )
        
        assert violation.timestamp == custom_time