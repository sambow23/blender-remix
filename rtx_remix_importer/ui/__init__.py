import bpy
from .properties import (
    register_properties,
    unregister_properties,
)
from .operators.project_ops import (
    LoadRemixProject,
    SetTargetSublayer,
    CreateRemixSublayer,
    AddRemixSublayer,
    CreateRemixModFile,
)
from .operators.sync_ops import (
    ApplyRemixModChanges,
)
from .operators.asset_ops import (
    SelectObjectByName,
    InvalidateRemixSingleAsset,
)
from .operators.capture_ops import (
    ScanCaptureFolder,
    ImportCaptureFile,
    ClearCaptureList,
    ToggleCaptureSelection,
    BatchImportCaptures,
    BatchImportSelectedCaptures,
)
from .operators.utility_ops import (
    ClearMaterialCache,
    FixBrokenTextures,
)
from .operators.camera_ops import (
    AlignViewToCamera,
)
from .project_panel import (
    PT_RemixProjectPanel,
)
from .asset_panel import (
    PT_RemixAssetProcessingPanel,
)
from .capture_panel import (
    PT_RemixCapturePanel,
    PT_RemixBackgroundProcessing,
    register_previews,
    unregister_previews,
    on_depsgraph_update,
)
from .capture_list import (
    RemixCaptureListItem,
    REMIX_UL_CaptureList,
)

# List of all operator classes for registration
operator_classes = [
    LoadRemixProject,
    SetTargetSublayer,
    CreateRemixSublayer,
    AddRemixSublayer,
    CreateRemixModFile,
    ApplyRemixModChanges,
    SelectObjectByName,
    InvalidateRemixSingleAsset,
    ScanCaptureFolder,
    ImportCaptureFile,
    ClearCaptureList,
    ToggleCaptureSelection,
    BatchImportCaptures,
    BatchImportSelectedCaptures,
    ClearMaterialCache,
    FixBrokenTextures,
    AlignViewToCamera,
]

# List of all panel classes for registration
panel_classes = [
    PT_RemixProjectPanel,
    PT_RemixAssetProcessingPanel,
    PT_RemixCapturePanel,
    PT_RemixBackgroundProcessing,
]

# List of all UIList classes
ui_list_classes = [
    REMIX_UL_CaptureList,
]

# List of all PropertyGroup classes
property_group_classes = [
    RemixCaptureListItem,
]

def register():
    """Register all UI components."""
    for cls in property_group_classes:
        bpy.utils.register_class(cls)
    register_properties()
    for cls in operator_classes:
        bpy.utils.register_class(cls)
    for cls in ui_list_classes:
        bpy.utils.register_class(cls)
    for cls in panel_classes:
        bpy.utils.register_class(cls)
    register_previews()
    bpy.app.handlers.depsgraph_update_post.append(on_depsgraph_update)

def unregister():
    """Unregister all UI components."""
    bpy.app.handlers.depsgraph_update_post.remove(on_depsgraph_update)
    unregister_previews()
    for cls in reversed(panel_classes):
        bpy.utils.unregister_class(cls)
    for cls in reversed(ui_list_classes):
        bpy.utils.unregister_class(cls)
    for cls in reversed(operator_classes):
        bpy.utils.unregister_class(cls)
    unregister_properties()
    for cls in reversed(property_group_classes):
        bpy.utils.unregister_class(cls) 