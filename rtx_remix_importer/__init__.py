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
import os
from bpy_extras.io_utils import ImportHelper, ExportHelper
from bpy.props import StringProperty, BoolProperty, CollectionProperty, EnumProperty, FloatProperty
from bpy.types import Operator

# Attempt to import core functionalities and check for USD availability
try:
    from . import import_core
    from . import material_utils
    from . import light_utils
    from . import texture_utils
    from . import usd_utils
    from . import constants
    from . import core_utils
    from .usd_utils import USD_AVAILABLE
    from .operators import export_operator
    from .ui import panels
    try:
        from .operators.export_operator import ExportRemixModFile, InvalidateRemixAssets
    except ImportError:
        ExportRemixModFile = None # Define as None if import fails
        InvalidateRemixAssets = None
except ImportError as e:
    print(f"Error importing addon submodules: {e}")
    # Handle cases where submodules might be missing or cause errors on registration
    USD_AVAILABLE = False # Assume USD is not available if imports fail
    export_operator = None # Ensure it's defined even on import failure
    panels = None # Ensure it's defined even on import failure


# --- Addon Registration ---

# Dynamically build the list of classes to register
classes_to_register = []

if export_operator and hasattr(export_operator, 'ExportRemixAsset'):
    classes_to_register.append(export_operator.ExportRemixAsset)
# Add the new hotload exporter
if ExportRemixModFile: # Check if it was imported successfully
    classes_to_register.append(ExportRemixModFile)
# Add the new invalidate assets operator
if InvalidateRemixAssets: # Check if it was imported successfully
    classes_to_register.append(InvalidateRemixAssets)

if panels:
    if hasattr(panels, 'LoadRemixProject'):
        classes_to_register.append(panels.LoadRemixProject)
    if hasattr(panels, 'PT_RemixProjectPanel'):
        classes_to_register.append(panels.PT_RemixProjectPanel)
    if hasattr(panels, 'CreateRemixSublayer'):
        classes_to_register.append(panels.CreateRemixSublayer)
    if hasattr(panels, 'AddRemixSublayer'):
        classes_to_register.append(panels.AddRemixSublayer)
    if hasattr(panels, 'SetTargetSublayer'):
        classes_to_register.append(panels.SetTargetSublayer)
    if hasattr(panels, 'PT_RemixObjectSettings'):
        classes_to_register.append(panels.PT_RemixObjectSettings)
    if hasattr(panels, 'CreateRemixModFile'):
        classes_to_register.append(panels.CreateRemixModFile)
    # Add the new ApplyRemixModChanges operator
    if hasattr(panels, 'ApplyRemixModChanges'):
        classes_to_register.append(panels.ApplyRemixModChanges)
    # Add the new Asset Processing panel and operators
    if hasattr(panels, 'PT_RemixAssetProcessingPanel'):
        classes_to_register.append(panels.PT_RemixAssetProcessingPanel)
    if hasattr(panels, 'SelectObjectByName'):
        classes_to_register.append(panels.SelectObjectByName)
    if hasattr(panels, 'InvalidateRemixSingleAsset'):
        classes_to_register.append(panels.InvalidateRemixSingleAsset)
    # Add the new Capture Management classes
    if hasattr(panels, 'ScanCaptureFolder'):
        classes_to_register.append(panels.ScanCaptureFolder)
    if hasattr(panels, 'ImportCaptureFile'):
        classes_to_register.append(panels.ImportCaptureFile)
    if hasattr(panels, 'ClearCaptureList'):
        classes_to_register.append(panels.ClearCaptureList)
    if hasattr(panels, 'ClearMaterialCache'):
        classes_to_register.append(panels.ClearMaterialCache)
    if hasattr(panels, 'BatchImportCaptures'):
        classes_to_register.append(panels.BatchImportCaptures)
    if hasattr(panels, 'ToggleCaptureSelection'):
        classes_to_register.append(panels.ToggleCaptureSelection)
    if hasattr(panels, 'BatchImportSelectedCaptures'):
        classes_to_register.append(panels.BatchImportSelectedCaptures)
    if hasattr(panels, 'PT_RemixCapturePanel'):
        classes_to_register.append(panels.PT_RemixCapturePanel)

classes_tuple = tuple(classes_to_register)

def register():
    # Check USD availability first
    if not USD_AVAILABLE:
        print("RTX Remix Importer: USD Libraries not found, registration skipped.")
        return

    # Register Scene Properties
    if panels and hasattr(panels, 'register_properties'):
        panels.register_properties()

    # Rebuild the classes tuple on register in case of reload issues
    global classes_tuple
    local_classes = []
    modules_to_reload = []
    if export_operator:
         modules_to_reload.append(export_operator)
    if panels:
         modules_to_reload.append(panels)
    # Try reloading modules during registration (helps with dev workflow)
    try:
        from importlib import reload
        for mod in modules_to_reload:
             reload(mod)
    except Exception as reload_err:
        print(f"Could not reload submodules during registration: {reload_err}")

    # Add classes after potential reload
    if export_operator and hasattr(export_operator, 'ExportRemixAsset'):
        local_classes.append(export_operator.ExportRemixAsset)
    # Add the test operator for transform application
    if export_operator and hasattr(export_operator, 'TestApplyAllTransforms'):
        local_classes.append(export_operator.TestApplyAllTransforms)
    # Add the new hotload exporter after reload
    if ExportRemixModFile:
         local_classes.append(ExportRemixModFile)
    # Add the invalidate assets operator after reload
    if InvalidateRemixAssets:
         local_classes.append(InvalidateRemixAssets)
    if panels:
        if hasattr(panels, 'LoadRemixProject'):
            local_classes.append(panels.LoadRemixProject)
        if hasattr(panels, 'PT_RemixProjectPanel'):
            local_classes.append(panels.PT_RemixProjectPanel)
        if hasattr(panels, 'CreateRemixSublayer'):
            local_classes.append(panels.CreateRemixSublayer)
        if hasattr(panels, 'AddRemixSublayer'):
            local_classes.append(panels.AddRemixSublayer)
        if hasattr(panels, 'SetTargetSublayer'):
            local_classes.append(panels.SetTargetSublayer)
        if hasattr(panels, 'PT_RemixObjectSettings'):
            local_classes.append(panels.PT_RemixObjectSettings)
        if hasattr(panels, 'CreateRemixModFile'):
            local_classes.append(panels.CreateRemixModFile)
        # Add the new ApplyRemixModChanges operator after reload
        if hasattr(panels, 'ApplyRemixModChanges'):
            local_classes.append(panels.ApplyRemixModChanges)
        # Add the new Asset Processing panel and operators after reload
        if hasattr(panels, 'PT_RemixAssetProcessingPanel'):
            local_classes.append(panels.PT_RemixAssetProcessingPanel)
        if hasattr(panels, 'SelectObjectByName'):
            local_classes.append(panels.SelectObjectByName)
        if hasattr(panels, 'InvalidateRemixSingleAsset'):
            local_classes.append(panels.InvalidateRemixSingleAsset)
        # Add the new Capture Management classes after reload
        if hasattr(panels, 'ScanCaptureFolder'):
            local_classes.append(panels.ScanCaptureFolder)
        if hasattr(panels, 'ImportCaptureFile'):
            local_classes.append(panels.ImportCaptureFile)
        if hasattr(panels, 'ClearCaptureList'):
            local_classes.append(panels.ClearCaptureList)
        if hasattr(panels, 'ClearMaterialCache'):
            local_classes.append(panels.ClearMaterialCache)
        if hasattr(panels, 'BatchImportCaptures'):
            local_classes.append(panels.BatchImportCaptures)
        if hasattr(panels, 'ToggleCaptureSelection'):
            local_classes.append(panels.ToggleCaptureSelection)
        if hasattr(panels, 'BatchImportSelectedCaptures'):
            local_classes.append(panels.BatchImportSelectedCaptures)
        if hasattr(panels, 'PT_RemixCapturePanel'):
            local_classes.append(panels.PT_RemixCapturePanel)

    classes_tuple = tuple(local_classes)

    # Register classes
    for cls in classes_tuple:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
             # Already registered, possibly due to reload
             print(f"Class {cls.__name__} already registered? Skipping.")
             # Ensure it's unregistered first for safety during reloads
             try:
                 bpy.utils.unregister_class(cls)
                 bpy.utils.register_class(cls)
             except Exception as re_reg_err:
                 print(f"Error re-registering class {cls.__name__}: {re_reg_err}")
        except Exception as e:
             print(f"Error registering class {cls.__name__}: {e}")

    print(f"RTX Remix Importer registered successfully with classes: {[c.__name__ for c in classes_tuple]}")

def unregister():
    # Unregister classes in reverse order
    global classes_tuple
    for cls in reversed(classes_tuple):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
             print(f"Warning: Class {cls.__name__} not registered? Skipping.")
        except Exception as e:
             print(f"Error unregistering class {cls.__name__}: {e}")

    # Unregister Scene Properties
    if panels and hasattr(panels, 'unregister_properties'):
        try:
            panels.unregister_properties()
        except Exception as e:
            print(f"Error unregistering properties: {e}")

    # Cleanup addon resources
    try:
        from . import core_utils
        core_utils.cleanup_addon_resources()
    except ImportError:
        pass  # Module might not be available

    print("RTX Remix Importer unregistered.")


# --- Development Auto-Reload ---
if "bpy" in locals() and __name__ != "__main__":
    # Add all relevant modules here for reliable reloading
    modules_to_reload = []
    if 'import_core' in locals(): modules_to_reload.append(import_core)
    if 'material_utils' in locals(): modules_to_reload.append(material_utils)
    if 'light_utils' in locals(): modules_to_reload.append(light_utils)
    if 'texture_utils' in locals(): modules_to_reload.append(texture_utils)
    if 'usd_utils' in locals(): modules_to_reload.append(usd_utils)
    if 'constants' in locals(): modules_to_reload.append(constants)
    if 'core_utils' in locals(): modules_to_reload.append(core_utils)
    if 'material_processor' in locals(): modules_to_reload.append(material_processor)
    if 'export_utils' in locals(): modules_to_reload.append(export_utils)
    if 'export_operator' in locals(): modules_to_reload.append(export_operator)
    if 'panels' in locals(): modules_to_reload.append(panels)
    # Add the new mod_apply_utils to the reload list
    try:
        from . import mod_apply_utils
        if 'mod_apply_utils' in locals(): modules_to_reload.append(mod_apply_utils)
    except ImportError:
        print("Skipping mod_apply_utils in reload list, not found (expected during initial creation).")

    try:
        from importlib import reload
        print("Reloading RTX Remix Importer submodules...")
        for mod in modules_to_reload:
            reload(mod)
            print(f"  Reloaded: {mod.__name__}")
        print("Submodule reload complete.")
    except Exception as e:
        print(f"Error reloading submodules: {e}")