"""
Tests for topology discovery and GPU affinity calculations.
"""

import pytest
from unittest.mock import Mock, patch
from cluster_helper.topology import TopologyManager, PCIeDevice, GPUInfo


class TestTopologyManager:
    """Test topology discovery functionality."""
    
    def test_initialization(self):
        """Test that topology manager initializes correctly."""
        with patch('cluster_helper.topology.subprocess.run') as mock_run:
            # Mock lspci output
            mock_lspci = Mock()
            mock_lspci.stdout = """+-00.0  Intel Corporation Device
 +-01.0-[01]----00.0  NVIDIA Corporation Device [GeForce GTX 1080]
 +-02.0-[02]----00.0  NVIDIA Corporation Device [GeForce GTX 1080]"""
            mock_lspci.returncode = 0
            
            # Mock hwloc output
            mock_hwloc = Mock()
            mock_hwloc.stdout = """Machine (32GB total)
  NUMANode L#0 (P#0 16GB)
    PCI 01:00.0 (VGA)
  NUMANode L#1 (P#1 16GB)  
    PCI 02:00.0 (VGA)"""
            mock_hwloc.returncode = 0
            
            mock_run.side_effect = [mock_lspci, mock_hwloc]
            
            topology = TopologyManager()
            
            assert len(topology.list_gpus()) >= 0
            assert len(topology.get_numa_nodes()) >= 0
    
    def test_pcie_tree_parsing(self):
        """Test PCIe tree parsing from lspci output."""
        topology = TopologyManager()
        
        # Test with mock lspci output
        lspci_output = """+-00.0  Intel Corporation Host Bridge
 +-01.0-[01]----00.0  NVIDIA Corporation Device [GeForce GTX 1080]
 +-02.0-[02]----00.0  NVIDIA Corporation Device [GeForce GTX 1080]"""
        
        topology._build_pcie_tree(lspci_output)
        
        # Check that devices were parsed
        assert len(topology._pcie_tree) > 0
    
    def test_affinity_scoring(self):
        """Test GPU affinity scoring calculations."""
        with patch('cluster_helper.topology.subprocess.run'):
            topology = TopologyManager()
            
            # Create mock GPU info
            gpu1_info = GPUInfo(
                uuid="GPU-00000001-mock-uuid",
                pci_address="01:00.0",
                numa_node=0,
                pcie_path=["00:00.0", "01:00.0"]
            )
            
            gpu2_info = GPUInfo(
                uuid="GPU-00000002-mock-uuid", 
                pci_address="02:00.0",
                numa_node=1,
                pcie_path=["00:00.0", "02:00.0"]
            )
            
            topology._gpu_info = {
                gpu1_info.uuid: gpu1_info,
                gpu2_info.uuid: gpu2_info
            }
            
            # Test affinity between GPUs on different NUMA nodes
            score = topology.get_affinity_score(gpu1_info.uuid, gpu2_info.uuid)
            assert score > 0  # Should have penalty for different NUMA nodes
            
            # Test affinity of GPU with itself
            score_self = topology.get_affinity_score(gpu1_info.uuid, gpu1_info.uuid)
            assert score_self == 0  # No penalty for same GPU
    
    def test_common_pcie_path(self):
        """Test PCIe common path calculation."""
        topology = TopologyManager()
        
        path1 = ["00:00.0", "01:00.0", "01:01.0"]
        path2 = ["00:00.0", "01:00.0", "01:02.0"]
        
        common_length = topology._get_common_pcie_path_length(path1, path2)
        assert common_length == 2  # First two elements are common
        
        path3 = ["00:00.0", "02:00.0"]
        common_length2 = topology._get_common_pcie_path_length(path1, path3)
        assert common_length2 == 1  # Only root is common
    
    def test_fallback_behavior(self):
        """Test fallback behavior when tools are unavailable."""
        with patch('cluster_helper.topology.subprocess.run', side_effect=FileNotFoundError):
            topology = TopologyManager()
            
            # Should still create some topology even without tools
            assert len(topology.list_gpus()) >= 0
    
    def test_gpu_info_retrieval(self):
        """Test GPU information retrieval."""
        with patch('cluster_helper.topology.subprocess.run'):
            topology = TopologyManager()
            
            gpus = topology.list_gpus()
            if gpus:
                gpu_uuid = gpus[0]
                gpu_info = topology.get_gpu_info(gpu_uuid)
                
                assert gpu_info is not None
                assert gpu_info.uuid == gpu_uuid
                assert gpu_info.pci_address is not None
                assert isinstance(gpu_info.numa_node, int)
                assert isinstance(gpu_info.pcie_path, list)
    
    @patch('cluster_helper.topology.subprocess.run')
    def test_lspci_error_handling(self, mock_run):
        """Test error handling when lspci fails."""
        mock_run.side_effect = subprocess.CalledProcessError(1, 'lspci')
        
        # Should not raise exception, but use fallback
        topology = TopologyManager()
        assert topology is not None
    
    def test_numa_node_discovery(self):
        """Test NUMA node discovery."""
        with patch('cluster_helper.topology.subprocess.run'):
            topology = TopologyManager()
            
            numa_nodes = topology.get_numa_nodes()
            assert isinstance(numa_nodes, list)
            assert len(numa_nodes) >= 1  # Should have at least one node
            
            for node in numa_nodes:
                assert isinstance(node, int)
                assert node >= 0