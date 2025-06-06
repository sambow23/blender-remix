import bpy

class RemixCaptureListItem(bpy.types.PropertyGroup):
    """Group of properties representing an item in the remix_captures list."""
    name: bpy.props.StringProperty(name="Name", description="Name of the capture file", default="Unknown")
    full_path: bpy.props.StringProperty(name="Full Path", description="Full path to the capture file", default="")
    size_mb: bpy.props.FloatProperty(name="Size (MB)", description="File size in megabytes", default=0.0)
    is_selected: bpy.props.BoolProperty(name="Is Selected", description="Is this capture selected for batch import", default=False)

class REMIX_UL_CaptureList(bpy.types.UIList):
    """UIList for displaying the list of available captures."""
    bl_idname = "REMIX_UL_capture_list"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        scene = data
        capture = item

        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)

            # Checkbox for batch selection
            row.prop(capture, "is_selected", text="", emboss=False)

            # File icon and name
            if capture.name.lower().endswith('.usd'):
                icon = 'FILE_3D'
            elif capture.name.lower().endswith('.usda'):
                icon = 'FILE_TEXT'
            elif capture.name.lower().endswith('.usdc'):
                icon = 'FILE_CACHE'
            else:
                icon = 'FILE'
            
            # Truncate long filenames
            display_name = capture.name if len(capture.name) <= 30 else capture.name[:27] + "..."
            row.label(text=f"{display_name} ({capture.size_mb:.1f}MB)", icon=icon)

            # Import button
            import_op = row.operator("remix.import_capture_file", text="", icon='IMPORT')
            import_op.capture_file_path = capture.full_path

    def filter_items(self, context, data, propname):
        captures = getattr(data, propname)
        helper = bpy.types.UI_UL_list

        # Filtering
        filter_name = self.filter_name.lower()
        if filter_name:
            flt_flags = [self.bitflag_filter_item if filter_name in c.name.lower() else 0 for c in captures]
        else:
            flt_flags = [self.bitflag_filter_item] * len(captures)
            
        # Sorting
        if self.use_filter_sort_alpha:
            # Create a list of (name, index) tuples for sorting
            s_items = [(c.name.lower(), i) for i, c in enumerate(captures)]
            # Sort the list
            s_items.sort(key=lambda x: x[0])
            # Get the new order of indices
            flt_neworder = [item[1] for item in s_items]
        else:
            flt_neworder = list(range(len(captures)))

        # Reverse sort
        if self.use_filter_sort_reverse:
            flt_neworder.reverse()
        
        return flt_flags, flt_neworder

classes = (
    RemixCaptureListItem,
    REMIX_UL_CaptureList,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls) 