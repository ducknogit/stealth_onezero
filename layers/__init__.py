"""
stealth_onezero.layers — Anti-detection disguise modules

Layers:
- disguise: Process identity, Disk artifacts, Memory, Threads (Layer 1-4)
- gpu_pattern: GPU behavior disguise (Layer 5)
- network_disguise: Network disguise (Layer 6)
- orchestrator: Coordination + Training simulator
"""

__version__ = "1.0.0"
__all__ = [
    "disguise",
    "gpu_pattern", 
    "network_disguise",
    "orchestrator",
]
