bl_info = {
    "name": "Remix Toolkit for Blender",
    "author": "CR",
    "version": (0, 1),
    "blender": (4, 0, 0),
    "description": "A replacement to the official NVIDIA toolkit. Imports RTX Remix captures and export assets back in a NVIDIA Omniverse USD compatible format.",
    "doc_url": "",
    "category": "Import-Export",
}

import bpy
from . import ui
from . import operators

def register():
    operators.register()
    ui.register()

def unregister():
    ui.unregister()
    operators.unregister()