"""Application, adapter, transport contract and document formats are independent."""
from . import __version__

ADAPTER_VERSION = '1.0.0'
CONTRACT_VERSION = 'headless-stream-v1'
WORKFLOW_FORMAT = 'novel-studio-workflow-v1'
WORKSPACE_FORMAT = 'novel-studio-dual-workspace-v1'
CURRENT_SCHEMA = 2
READABLE_SCHEMAS = (1, 2)


def versions(core=None):
    value = {'software': __version__, 'adapters': {'antigravity_cli': ADAPTER_VERSION, 'official_api': 'official-api-v1'},
        'transport_contract': CONTRACT_VERSION, 'workspace_format': WORKSPACE_FORMAT,
        'workflow_format': WORKFLOW_FORMAT, 'readable_schemas': list(READABLE_SCHEMAS), 'new_project_schema': CURRENT_SCHEMA}
    if core:
        try:
            state = core.workflow.store.read()
            value['project_schema'] = state.get('product_schema', 1)
        except ValueError:
            value['project_schema'] = 'UNSUPPORTED'
    return value
