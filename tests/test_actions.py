"""
Tests for action execution and mitigation strategies.
"""

import pytest
import tempfile
from unittest.mock import Mock, patch, mock_open
from pathlib import Path

from cluster_helper.actions import ActionExecutor, ActionType, ActionResult
from cluster_helper.state import Violation


class TestActionExecutor:
    """Test action execution functionality."""
    
    def test_action_executor_initialization(self):
        """Test action executor initialization."""
        executor = ActionExecutor(max_io_limit_mbps=500, enable_mig=False)
        
        assert executor.max_io_limit_mbps == 500
        assert executor.enable_mig == False
        assert len(executor._action_history) == 0
    
    def test_io_limit_calculation(self):
        """Test I/O limit calculation based on violation severity."""
        executor = ActionExecutor(max_io_limit_mbps=1000)
        
        # Test different severity levels
        limit_low = executor._calculate_io_limit(0.2)    # Low severity
        limit_high = executor._calculate_io_limit(1.5)   # High severity
        
        assert limit_high < limit_low  # Higher severity = lower limit
        assert limit_low > 0
        assert limit_high > 0
    
    @patch('cluster_helper.actions.Path')
    def test_process_cgroup_discovery(self, mock_path):
        """Test finding cgroup path for a process."""
        executor = ActionExecutor()
        
        # Mock /proc/PID/cgroup content
        cgroup_content = "0::/user.slice/user-1000.slice/session-1.scope"
        
        mock_cgroup_file = Mock()
        mock_cgroup_file.exists.return_value = True
        mock_cgroup_file.read_text.return_value = cgroup_content
        
        mock_path_instance = Mock()
        mock_path_instance.__truediv__ = Mock(return_value=mock_cgroup_file)
        mock_path.return_value = mock_path_instance
        
        # Mock the resulting cgroup path exists
        with patch('pathlib.Path.exists', return_value=True):
            cgroup_path = executor._find_process_cgroup(1234)
            
        assert cgroup_path is not None
        assert "cgroup" in cgroup_path
    
    def test_block_device_discovery(self):
        """Test block device discovery for I/O limiting."""
        executor = ActionExecutor()
        
        # Mock /proc/partitions content
        partitions_content = """major minor  #blocks  name
   8        0  488386584 sda
   8        1     524288 sda1
 259        0  500107608 nvme0n1
 259        1     524288 nvme0n1p1"""
        
        with patch('pathlib.Path.read_text', return_value=partitions_content):
            devices = executor._get_block_devices()
            
        assert len(devices) > 0
        assert "8:0" in devices or "259:0" in devices  # Should find main devices
    
    @patch('cluster_helper.actions.subprocess.run')
    def test_mig_reconfiguration_success(self, mock_run):
        """Test successful MIG reconfiguration."""
        executor = ActionExecutor(enable_mig=True)
        
        # Mock successful nvidia-smi commands
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Success"
        mock_result.stderr = ""
        mock_run.return_value = mock_result
        
        result = executor.reconfigure_mig_profile("GPU-00000001-mock-uuid", "1g.5gb:4")
        
        assert result.success == True
        assert result.action_type == ActionType.MIG_RECONFIGURE
        assert "GPU-00000001-mock-uuid" in result.target_gpu
    
    @patch('cluster_helper.actions.subprocess.run')
    def test_mig_reconfiguration_failure(self, mock_run):
        """Test failed MIG reconfiguration."""
        executor = ActionExecutor(enable_mig=True)
        
        # Mock failed nvidia-smi command
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "MIG not supported"
        mock_run.return_value = mock_result
        
        result = executor.reconfigure_mig_profile("GPU-00000001-mock-uuid", "1g.5gb:4")
        
        assert result.success == False
        assert result.action_type == ActionType.MIG_RECONFIGURE
        assert "not supported" in result.message or "Failed" in result.message
    
    def test_mig_disabled(self):
        """Test MIG reconfiguration when disabled."""
        executor = ActionExecutor(enable_mig=False)
        
        result = executor.reconfigure_mig_profile("GPU-00000001-mock-uuid", "1g.5gb:4")
        
        assert result.success == False
        assert "disabled" in result.message
    
    def test_gpu_uuid_to_index_conversion(self):
        """Test GPU UUID to index conversion."""
        executor = ActionExecutor()
        
        # Test mock UUID
        index = executor._gpu_uuid_to_index("GPU-00000001-mock-uuid")
        assert index == 1
        
        # Test fallback
        index = executor._gpu_uuid_to_index("unknown-uuid")
        assert index == 0
    
    def test_mig_profile_selection(self):
        """Test MIG profile selection based on violation severity."""
        executor = ActionExecutor()
        
        # Create test violations with different severities
        low_violation = Violation(
            victim_pid=1234,
            victim_gpu="GPU-00000001-mock-uuid",
            bully_pids=[5678],
            violation_severity=0.3
        )
        
        high_violation = Violation(
            victim_pid=1234,
            victim_gpu="GPU-00000001-mock-uuid", 
            bully_pids=[5678],
            violation_severity=1.5
        )
        
        low_profile = executor._select_mig_profile(low_violation)
        high_profile = executor._select_mig_profile(high_violation)
        
        assert low_profile != high_profile  # Should select different profiles
        assert "g." in low_profile  # Should be valid MIG format
        assert "g." in high_profile
    
    @patch('cluster_helper.actions.ActionExecutor.apply_cgroup_io_limit')
    @patch('cluster_helper.actions.ActionExecutor.reconfigure_mig_profile')
    def test_violation_mitigation_tiered_response(self, mock_mig, mock_cgroup):
        """Test tiered response to violations."""
        executor = ActionExecutor(enable_mig=True)
        
        # Mock successful cgroup application
        mock_cgroup.return_value = ActionResult(
            action_type=ActionType.CGROUP_IO_LIMIT,
            success=True,
            message="Applied",
            timestamp=0,
            target_pid=5678
        )
        
        # Mock successful MIG reconfiguration  
        mock_mig.return_value = ActionResult(
            action_type=ActionType.MIG_RECONFIGURE,
            success=True,
            message="Reconfigured",
            timestamp=0,
            target_gpu="GPU-00000001-mock-uuid"
        )
        
        # Create high-severity violation
        violation = Violation(
            victim_pid=1234,
            victim_gpu="GPU-00000001-mock-uuid",
            bully_pids=[5678, 9012],
            violation_severity=0.8  # High enough to trigger MIG
        )
        
        results = executor.mitigate_violation(violation)
        
        # Should apply cgroup limits to bullies AND trigger MIG
        assert len(results) >= 2  # At least cgroup + MIG
        
        # Check that cgroup was called for each bully
        assert mock_cgroup.call_count == 2  # Two bullies
        
        # Check that MIG was called
        assert mock_mig.call_count == 1
    
    def test_action_history_tracking(self):
        """Test action history tracking."""
        executor = ActionExecutor()
        
        # Create some test action results
        result1 = ActionResult(
            action_type=ActionType.CGROUP_IO_LIMIT,
            success=True,
            message="Test 1",
            timestamp=0
        )
        
        result2 = ActionResult(
            action_type=ActionType.MIG_RECONFIGURE,
            success=False,
            message="Test 2", 
            timestamp=1
        )
        
        executor._action_history.extend([result1, result2])
        
        # Test getting all history
        history = executor.get_action_history()
        assert len(history) == 2
        
        # Test filtering by action type
        cgroup_history = executor.get_action_history(ActionType.CGROUP_IO_LIMIT)
        assert len(cgroup_history) == 1
        assert cgroup_history[0].action_type == ActionType.CGROUP_IO_LIMIT
        
        # Test limit
        limited_history = executor.get_action_history(limit=1)
        assert len(limited_history) == 1
    
    def test_action_statistics(self):
        """Test action statistics generation."""
        executor = ActionExecutor()
        
        # Add some test actions
        results = [
            ActionResult(ActionType.CGROUP_IO_LIMIT, True, "Success 1", 0),
            ActionResult(ActionType.CGROUP_IO_LIMIT, False, "Failed 1", 1),
            ActionResult(ActionType.MIG_RECONFIGURE, True, "Success 2", 2),
        ]
        
        executor._action_history.extend(results)
        
        stats = executor.get_action_stats()
        
        assert stats['total_actions'] == 3
        assert stats['successful_actions'] == 2
        assert stats['failed_actions'] == 1
        assert stats['cgroup_io_limit_total'] == 2
        assert stats['cgroup_io_limit_successful'] == 1
        assert stats['mig_reconfigure_total'] == 1
        assert stats['mig_reconfigure_successful'] == 1
    
    @patch('cluster_helper.actions.ActionExecutor._find_process_cgroup')
    @patch('cluster_helper.actions.ActionExecutor._write_cgroup_io_limit')
    def test_cgroup_io_limit_success(self, mock_write, mock_find):
        """Test successful cgroup I/O limit application."""
        executor = ActionExecutor()
        
        # Mock finding cgroup path
        mock_find.return_value = "/sys/fs/cgroup/test"
        
        # Mock successful write
        mock_write.return_value = (True, "Applied successfully")
        
        result = executor.apply_cgroup_io_limit(1234, 100 * 1024 * 1024)
        
        assert result.success == True
        assert result.action_type == ActionType.CGROUP_IO_LIMIT
        assert result.target_pid == 1234
    
    @patch('cluster_helper.actions.ActionExecutor._find_process_cgroup')
    def test_cgroup_io_limit_no_cgroup(self, mock_find):
        """Test cgroup I/O limit when cgroup not found."""
        executor = ActionExecutor()
        
        # Mock not finding cgroup path
        mock_find.return_value = None
        
        result = executor.apply_cgroup_io_limit(1234, 100 * 1024 * 1024)
        
        assert result.success == False
        assert "Could not find cgroup" in result.message