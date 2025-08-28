"""
Main daemon entry point for cluster-helper.

This module implements the main control loop that coordinates all components
to monitor GPU workloads and apply SLO-aware mitigation actions.
"""

import logging
import signal
import sys
import time
from typing import Optional

from .config import ControllerConfig
from .topology import TopologyManager
from .metrics import MetricsMonitor
from .state import StateManager
from .actions import ActionExecutor


logger = logging.getLogger(__name__)


class ClusterHelperDaemon:
    """Main daemon class for cluster-helper service."""
    
    def __init__(self, config_path: Optional[str] = None):
        """Initialize the daemon with all necessary components.
        
        Args:
            config_path: Path to configuration file (optional)
        """
        self.config = ControllerConfig(config_path)
        self.running = False
        
        # Initialize components
        self.topology_manager: Optional[TopologyManager] = None
        self.metrics_monitor: Optional[MetricsMonitor] = None
        self.state_manager: Optional[StateManager] = None
        self.action_executor: Optional[ActionExecutor] = None
        
        # Setup logging
        self._setup_logging()
        
        # Setup signal handlers for graceful shutdown
        self._setup_signal_handlers()
        
        logger.info("ClusterHelperDaemon initialized")
    
    def _setup_logging(self) -> None:
        """Configure logging for the daemon."""
        # Configure root logger
        logging.basicConfig(
            level=getattr(logging, self.config.log_level),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(sys.stdout),  # Log to stdout for systemd
            ]
        )
        
        # Set specific log levels for components
        logging.getLogger('cluster_helper').setLevel(getattr(logging, self.config.log_level))
        
        logger.info(f"Logging configured at level: {self.config.log_level}")
    
    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, initiating graceful shutdown")
            self.stop()
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    def initialize_components(self) -> None:
        """Initialize all daemon components."""
        try:
            logger.info("Initializing daemon components...")
            
            # Initialize topology manager
            logger.info("Initializing topology manager...")
            self.topology_manager = TopologyManager()
            
            # Initialize metrics monitor
            logger.info("Initializing metrics monitor...")
            self.metrics_monitor = MetricsMonitor()
            
            # Initialize state manager
            logger.info("Initializing state manager...")
            self.state_manager = StateManager(
                tail_threshold_ms=self.config.tail_threshold_ms,
                persistence_windows=self.config.persistence_windows,
                cooldown_observations=self.config.cooldown_observations
            )
            
            # Initialize action executor
            logger.info("Initializing action executor...")
            self.action_executor = ActionExecutor(
                max_io_limit_mbps=self.config.max_cgroup_io_limit_mbps,
                enable_mig=self.config.enable_mig_reconfiguration
            )
            
            logger.info("All components initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize components: {e}")
            raise
    
    def run(self) -> None:
        """Run the main daemon loop."""
        logger.info("Starting cluster-helper daemon")
        
        try:
            self.initialize_components()
            
            self.running = True
            iteration = 0
            
            logger.info(f"Entering main loop (poll interval: {self.config.poll_interval_sec}s)")
            
            while self.running:
                iteration += 1
                loop_start = time.time()
                
                try:
                    self._run_monitoring_cycle(iteration)
                    
                except Exception as e:
                    logger.error(f"Error in monitoring cycle {iteration}: {e}")
                    # Continue running despite errors
                
                # Calculate sleep time to maintain consistent interval
                loop_duration = time.time() - loop_start
                sleep_time = max(0, self.config.poll_interval_sec - loop_duration)
                
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    logger.warning(f"Monitoring cycle {iteration} took {loop_duration:.2f}s "
                                 f"(longer than {self.config.poll_interval_sec}s interval)")
        
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        except Exception as e:
            logger.error(f"Fatal error in daemon: {e}")
            raise
        finally:
            self._cleanup()
    
    def _run_monitoring_cycle(self, iteration: int) -> None:
        """Execute one complete monitoring and mitigation cycle.
        
        Args:
            iteration: Current iteration number
        """
        cycle_start = time.time()
        
        logger.debug(f"Starting monitoring cycle {iteration}")
        
        # Step 1: Collect tenant latency metrics
        latencies = self.metrics_monitor.get_tenant_latencies()
        
        if not latencies:
            logger.debug("No tenant metrics available this cycle")
            return
        
        # Step 2: Update state manager with latest metrics
        violations = self.state_manager.update(latencies)
        
        # Step 3: Execute mitigation actions for any violations
        if violations:
            logger.warning(f"Processing {len(violations)} violations")
            
            for violation in violations:
                try:
                    results = self.action_executor.mitigate_violation(violation)
                    
                    successful_actions = sum(1 for r in results if r.success)
                    total_actions = len(results)
                    
                    logger.info(f"Violation mitigation for PID {violation.victim_pid}: "
                              f"{successful_actions}/{total_actions} actions successful")
                    
                except Exception as e:
                    logger.error(f"Failed to mitigate violation for PID {violation.victim_pid}: {e}")
        
        # Step 4: Cleanup stale metrics
        active_pids = set(latencies.keys())
        self.metrics_monitor.cleanup_stale_metrics(active_pids)
        
        # Log cycle summary
        cycle_duration = time.time() - cycle_start
        state_summary = self.state_manager.get_violation_summary()
        
        logger.info(f"Cycle {iteration} complete ({cycle_duration:.2f}s): "
                   f"{len(latencies)} tenants, {len(violations)} violations, "
                   f"states: {state_summary}")
    
    def stop(self) -> None:
        """Stop the daemon gracefully."""
        logger.info("Stopping cluster-helper daemon")
        self.running = False
    
    def _cleanup(self) -> None:
        """Perform cleanup operations."""
        logger.info("Performing cleanup...")
        
        try:
            # Log final statistics
            if self.action_executor:
                stats = self.action_executor.get_action_stats()
                logger.info(f"Final action statistics: {stats}")
            
            if self.state_manager:
                final_summary = self.state_manager.get_violation_summary()
                logger.info(f"Final state summary: {final_summary}")
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
        
        logger.info("Cluster-helper daemon stopped")


def run() -> None:
    """Entry point function for console script."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Cluster-Helper GPU SLO Controller')
    parser.add_argument(
        '--config', '-c',
        help='Path to configuration file',
        default='/etc/gpu-controller.conf'
    )
    parser.add_argument(
        '--debug', '-d',
        action='store_true',
        help='Enable debug logging'
    )
    
    args = parser.parse_args()
    
    # Override log level if debug requested
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        daemon = ClusterHelperDaemon(config_path=args.config)
        daemon.run()
    
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    run()