"""Provider command contracts. No import or planning operation contacts a provider."""
from .adapters import DaytonaAdapter, ModalAdapter, RailwayAdapter
from ..errors import OnedeployError


def get_adapter(name, manifest=None):
    adapters = {'daytona': DaytonaAdapter, 'railway': RailwayAdapter, 'modal': ModalAdapter}
    if name not in adapters:
        raise OnedeployError('unsupported', 'Unknown provider')
    transport = (manifest or {}).get('target', {}).get('transport')
    if transport is not None and (name != 'daytona' or transport not in {'cli', 'sdk'}):
        raise OnedeployError('invalid_config', 'target.transport supports cli or sdk only for Daytona')
    if name == 'daytona' and transport == 'sdk':
        from .adapters import DaytonaSDKAdapter
        return DaytonaSDKAdapter()
    return adapters[name]()
