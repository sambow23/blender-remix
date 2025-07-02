bl_info = {
    "name": "Remix Toolkit for Blender",
    "author": "CR",
    "version": (0, 3),
    "blender": (4, 0, 0),
    "description": "An alternative to the official NVIDIA RTX Remix Toolkit. Imports RTX Remix captures and export assets back in a RTX Remix compatible format.",
    "doc_url": "",
    "category": "Import-Export",
}

import bpy
from . import ui
from . import operators
from . import constants
from .core_utils import get_blender_version, is_blender_4_1_or_newer

# Import the native C++ module
try:
    # This is where we import our compiled C++ module.
    # The .so file must be in the same directory as this __init__.py
    from . import remix_native
    NATIVE_MODULE_LOADED = True
    print("RTX Remix Importer: Native C++ module loaded successfully")
    
    # Make native module available to other modules
    constants.NATIVE_MODULE_LOADED = True
    constants.remix_native = remix_native
    
except ImportError as e:
    print(f"RTX Remix Importer: Failed to import native module: {e}")
    print("RTX Remix Importer: Please build the native module first")
    NATIVE_MODULE_LOADED = False
    
    # Set constants accordingly
    constants.NATIVE_MODULE_LOADED = False
    constants.remix_native = None

def register():
    # Check if native module is available
    if not NATIVE_MODULE_LOADED:
        print("ERROR: RTX Remix Importer requires the native C++ module to be built and available")
        print("Please build the native module using the CMakeLists.txt in native/src/")
        return
        
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