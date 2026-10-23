"""Provider command contracts. No import or planning operation contacts a provider."""
from .adapters import DaytonaAdapter, ModalAdapter, RailwayAdapter
from ..errors import OnedeployError


def get_adapter(name):
    adapters = {'daytona': DaytonaAdapter, 'railway': RailwayAdapter, 'modal': ModalAdapter}
    if name not in adapters:
        raise OnedeployError('unsupported', 'Unknown provider')
    return adapters[name]()
