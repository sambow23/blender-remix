bl_info = {
    "name": "Remix Toolkit for Blender",
    "author": "CR",
    "version": (0, 1),
    "blender": (4, 0, 0),
    "description": "A replacement to the official NVIDIA toolkit. Imports RTX Remix captures and export assets back in a NVIDIA Omniverse USD compatible format. Compatible with Blender 4.0+ including 4.4+.",
    "doc_url": "",
    "category": "Import-Export",
}

import bpy
from . import ui
from . import operators
from .core_utils import get_blender_version, is_blender_4_1_or_newer

def register():
    # Check Blender version and show compatibility info
    version = get_blender_version()
    print(f"RTX Remix Importer: Running on Blender {version[0]}.{version[1]}.{version[2]}")
    
    if is_blender_4_1_or_newer():
        print("RTX Remix Importer: Blender 4.1+ detected - using compatibility mode for deprecated mesh methods")
    
    operators.register()
    ui.register()

def unregister():
    ui.unregister()
    operators.unregister()