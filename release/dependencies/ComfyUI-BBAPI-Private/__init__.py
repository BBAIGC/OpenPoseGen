"""BBAIGC BBAPI image nodes. Version V1.1.5."""
__version__ = "1.1.5"

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
try:
    from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
    from .connectors import NODE_CLASS_MAPPINGS as CONNECTORS, NODE_DISPLAY_NAME_MAPPINGS as CONNECTOR_NAMES
    NODE_CLASS_MAPPINGS.update(CONNECTORS)
    NODE_DISPLAY_NAME_MAPPINGS.update(CONNECTOR_NAMES)
    from . import routes
except Exception as error:
    import logging
    logging.getLogger("ComfyUI-BBAPI-Private").exception("节点加载失败：%s", error)

WEB_DIRECTORY = "./web"
for _node_class in NODE_CLASS_MAPPINGS.values():
    _node_class.CATEGORY = 'BBAIGC/BBAPI'
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
