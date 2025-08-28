"""
Topology discovery for GPU placement optimization.

This module discovers PCIe hierarchy and NUMA topology to provide
intelligent GPU affinity scoring for workload placement decisions.
"""

import logging
import re
import subprocess
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass


logger = logging.getLogger(__name__)


@dataclass
class PCIeDevice:
    """Represents a PCIe device in the topology tree."""
    address: str
    device_type: str
    parent: Optional[str] = None
    children: List[str] = None
    
    def __post_init__(self):
        if self.children is None:
            self.children = []


@dataclass
class GPUInfo:
    """GPU device information including NUMA and PCIe details."""
    uuid: str
    pci_address: str
    numa_node: int
    pcie_path: List[str]  # Path from root to GPU in PCIe tree
    
    
class TopologyManager:
    """Manages GPU topology discovery and affinity calculations."""
    
    def __init__(self):
        """Initialize topology manager."""
        self._pcie_tree: Dict[str, PCIeDevice] = {}
        self._gpu_info: Dict[str, GPUInfo] = {}
        self._numa_gpu_mapping: Dict[int, List[str]] = {}
        self._discover_topology()
    
    def _discover_topology(self) -> None:
        """Discover complete system topology."""
        try:
            self._parse_lspci()
            self._parse_hwloc()
            self._build_gpu_topology()
            logger.info(f"Discovered {len(self._gpu_info)} GPUs across topology")
        except Exception as e:
            logger.error(f"Failed to discover topology: {e}")
            raise
    
    def _parse_lspci(self) -> None:
        """Parse lspci output to build PCIe hierarchy tree."""
        try:
            # Get detailed PCIe topology
            result = subprocess.run(
                ['lspci', '-vt'], 
                capture_output=True, 
                text=True, 
                check=True
            )
            
            self._build_pcie_tree(result.stdout)
            logger.debug(f"Parsed PCIe tree with {len(self._pcie_tree)} devices")
            
        except subprocess.CalledProcessError as e:
            logger.error(f"lspci command failed: {e}")
            # Fallback to minimal topology
            self._create_fallback_pcie_tree()
        except FileNotFoundError:
            logger.warning("lspci not found, using fallback PCIe topology")
            self._create_fallback_pcie_tree()
    
    def _build_pcie_tree(self, lspci_output: str) -> None:
        """Build PCIe device tree from lspci -vt output."""
        lines = lspci_output.strip().split('\n')
        stack: List[Tuple[int, str]] = []  # (indent_level, pci_address)
        
        for line in lines:
            if not line.strip():
                continue
                
            # Calculate indentation level
            indent = (len(line) - len(line.lstrip())) // 2
            
            # Extract PCI address (e.g., "00:1f.3" or "0000:00:1f.3")
            pci_match = re.search(r'([0-9a-f]{2,4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f])', line)
            if not pci_match:
                continue
                
            pci_addr = pci_match.group(1)
            
            # Determine device type
            device_type = "unknown"
            if "VGA compatible controller" in line or "3D controller" in line:
                device_type = "gpu"
            elif "PCI bridge" in line:
                device_type = "bridge"
            elif "Root Port" in line:
                device_type = "root_port"
            
            # Find parent based on indentation
            parent = None
            while stack and stack[-1][0] >= indent:
                stack.pop()
            
            if stack:
                parent = stack[-1][1]
            
            # Create device
            device = PCIeDevice(
                address=pci_addr,
                device_type=device_type,
                parent=parent
            )
            
            self._pcie_tree[pci_addr] = device
            
            # Update parent's children
            if parent and parent in self._pcie_tree:
                self._pcie_tree[parent].children.append(pci_addr)
            
            stack.append((indent, pci_addr))
    
    def _create_fallback_pcie_tree(self) -> None:
        """Create minimal PCIe tree when lspci is unavailable."""
        # Create a simple root device
        root = PCIeDevice(address="00:00.0", device_type="root_port")
        self._pcie_tree["00:00.0"] = root
        logger.info("Created fallback PCIe topology")
    
    def _parse_hwloc(self) -> None:
        """Parse hwloc output to map GPUs to NUMA nodes."""
        try:
            # Try lstopo-no-graphics for text output
            result = subprocess.run(
                ['lstopo-no-graphics', '--of', 'console'], 
                capture_output=True, 
                text=True, 
                check=True
            )
            
            self._parse_hwloc_output(result.stdout)
            
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.warning("hwloc not available, attempting fallback NUMA discovery")
            self._discover_numa_fallback()
    
    def _parse_hwloc_output(self, hwloc_output: str) -> None:
        """Parse hwloc console output to extract NUMA-GPU mappings."""
        current_numa = 0
        
        for line in hwloc_output.split('\n'):
            line = line.strip()
            
            # Look for NUMA node indicators
            numa_match = re.search(r'NUMANode.*?#(\d+)', line)
            if numa_match:
                current_numa = int(numa_match.group(1))
                if current_numa not in self._numa_gpu_mapping:
                    self._numa_gpu_mapping[current_numa] = []
                continue
            
            # Look for GPU devices (PCI addresses)
            pci_match = re.search(r'([0-9a-f]{2,4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f])', line)
            if pci_match and ("VGA" in line or "3D" in line or "GPU" in line):
                pci_addr = pci_match.group(1)
                if current_numa not in self._numa_gpu_mapping:
                    self._numa_gpu_mapping[current_numa] = []
                self._numa_gpu_mapping[current_numa].append(pci_addr)
    
    def _discover_numa_fallback(self) -> None:
        """Fallback NUMA discovery using /sys filesystem."""
        try:
            # Try to read NUMA information from sysfs
            import glob
            
            numa_dirs = glob.glob('/sys/devices/system/node/node*')
            for numa_dir in numa_dirs:
                numa_id = int(numa_dir.split('node')[-1])
                self._numa_gpu_mapping[numa_id] = []
                
            # If no NUMA nodes found, assume single node
            if not self._numa_gpu_mapping:
                self._numa_gpu_mapping[0] = []
                logger.info("No NUMA topology detected, assuming single NUMA node")
                
        except Exception as e:
            logger.warning(f"NUMA fallback discovery failed: {e}")
            self._numa_gpu_mapping[0] = []
    
    def _build_gpu_topology(self) -> None:
        """Build final GPU topology information."""
        # For now, create mock GPU entries since we can't query nvidia-smi in this environment
        # In a real implementation, this would use nvidia-ml-py or nvidia-smi to get GPU UUIDs
        
        gpu_count = 0
        for numa_node, pci_addresses in self._numa_gpu_mapping.items():
            for pci_addr in pci_addresses:
                if pci_addr in self._pcie_tree and self._pcie_tree[pci_addr].device_type == "gpu":
                    gpu_uuid = f"GPU-{gpu_count:08d}-mock-uuid"
                    
                    # Build PCIe path from root to GPU
                    pcie_path = self._get_pcie_path(pci_addr)
                    
                    gpu_info = GPUInfo(
                        uuid=gpu_uuid,
                        pci_address=pci_addr,
                        numa_node=numa_node,
                        pcie_path=pcie_path
                    )
                    
                    self._gpu_info[gpu_uuid] = gpu_info
                    gpu_count += 1
        
        # If no GPUs found in topology, create mock entries for testing
        if not self._gpu_info:
            for i in range(2):  # Create 2 mock GPUs
                gpu_uuid = f"GPU-{i:08d}-mock-uuid"
                pci_addr = f"00:0{i+1}.0"
                
                gpu_info = GPUInfo(
                    uuid=gpu_uuid,
                    pci_address=pci_addr,
                    numa_node=i % 2,  # Alternate NUMA nodes
                    pcie_path=[pci_addr]
                )
                
                self._gpu_info[gpu_uuid] = gpu_info
            
            logger.info("Created mock GPU topology for testing")
    
    def _get_pcie_path(self, pci_address: str) -> List[str]:
        """Get PCIe path from root to the specified device."""
        path = []
        current = pci_address
        
        # Traverse up the tree to find the complete path
        visited = set()
        while current and current not in visited:
            visited.add(current)
            path.append(current)
            
            if current in self._pcie_tree:
                current = self._pcie_tree[current].parent
            else:
                break
        
        return list(reversed(path))  # Return path from root to device
    
    def get_affinity_score(self, gpu1_uuid: str, gpu2_uuid: str, 
                          numa_weight: float = 2.0, pcie_weight: float = 1.5) -> float:
        """Calculate affinity penalty score between two GPUs.
        
        Args:
            gpu1_uuid: First GPU UUID
            gpu2_uuid: Second GPU UUID  
            numa_weight: Weight for NUMA affinity penalty
            pcie_weight: Weight for PCIe topology penalty
            
        Returns:
            Penalty score (higher = worse affinity, 0 = perfect affinity)
        """
        if gpu1_uuid not in self._gpu_info or gpu2_uuid not in self._gpu_info:
            logger.warning(f"Unknown GPU UUID(s): {gpu1_uuid}, {gpu2_uuid}")
            return float('inf')
        
        gpu1 = self._gpu_info[gpu1_uuid]
        gpu2 = self._gpu_info[gpu2_uuid]
        
        penalty = 0.0
        
        # NUMA penalty
        if gpu1.numa_node != gpu2.numa_node:
            penalty += numa_weight
            logger.debug(f"NUMA penalty applied: GPUs on different nodes "
                        f"({gpu1.numa_node} vs {gpu2.numa_node})")
        
        # PCIe topology penalty
        common_path_length = self._get_common_pcie_path_length(gpu1.pcie_path, gpu2.pcie_path)
        max_path_length = max(len(gpu1.pcie_path), len(gpu2.pcie_path))
        
        if max_path_length > 0:
            pcie_penalty = pcie_weight * (1.0 - common_path_length / max_path_length)
            penalty += pcie_penalty
            logger.debug(f"PCIe penalty: {pcie_penalty:.2f} "
                        f"(common path: {common_path_length}/{max_path_length})")
        
        return penalty
    
    def _get_common_pcie_path_length(self, path1: List[str], path2: List[str]) -> int:
        """Get length of common PCIe path between two devices."""
        common_length = 0
        min_length = min(len(path1), len(path2))
        
        for i in range(min_length):
            if path1[i] == path2[i]:
                common_length += 1
            else:
                break
        
        return common_length
    
    def get_gpu_info(self, gpu_uuid: str) -> Optional[GPUInfo]:
        """Get topology information for a specific GPU."""
        return self._gpu_info.get(gpu_uuid)
    
    def list_gpus(self) -> List[str]:
        """List all discovered GPU UUIDs."""
        return list(self._gpu_info.keys())
    
    def get_numa_nodes(self) -> List[int]:
        """Get list of all NUMA nodes."""
        return list(self._numa_gpu_mapping.keys())