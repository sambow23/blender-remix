import bpy
from .operators.capture_ops import *
from .operators.utility_ops import *

class PT_RemixCapturePanel(bpy.types.Panel):
    """Panel for managing RTX Remix capture imports"""
    bl_label = "Captures"
    bl_idname = "SCENE_PT_remix_capture"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RTX Remix"
    bl_context = ""
    
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        # Capture Folder Selection
        box = layout.box()
        box.label(text="Capture Folder", icon='FOLDER_REDIRECT')
        row = box.row(align=True)
        row.prop(scene, "remix_capture_folder_path", text="")
        row.operator(ScanCaptureFolder.bl_idname, icon='FILE_REFRESH', text="Refresh")
        
        # Cache Management
        cache_row = box.row(align=True)
        cache_row.operator(ClearMaterialCache.bl_idname, icon='TRASH', text="Clear Cache")
        cache_row.operator(FixBrokenTextures.bl_idname, icon='TEXTURE', text="Fix Broken Textures")
        cache_row.scale_y = 0.8  # Make it smaller since it's a utility function
        
        # Import Settings
        if scene.remix_capture_folder_path:
            settings_box = layout.box()
            settings_box.label(text="Import Settings", icon='SETTINGS')
            
            col = settings_box.column(align=True)
            col.prop(scene, "remix_capture_scene_scale")
            col.prop(scene, "remix_capture_import_materials")
            col.prop(scene, "remix_capture_import_lights")
            
            # Advanced settings (collapsible)
            col.separator()
        
        # Available Captures List
        captures_box = layout.box()
        captures_box.label(text="Available Captures", icon='FILE_3D')
        
        # Get the scanned captures and selected captures
        available_captures = scene.get("_remix_available_captures", [])
        selected_captures = scene.get("_remix_batch_selected_captures", [])
        if not isinstance(selected_captures, list):
            selected_captures = []
        
        if not available_captures:
            if scene.remix_capture_folder_path:
                captures_box.label(text="No captures found. Folder scanned automatically.", icon='INFO')
            else:
                captures_box.label(text="Select a capture folder first.", icon='ERROR')
        else:
            # Show count and controls
            header_row = captures_box.row(align=True)
            header_row.label(text=f"Found: {len(available_captures)} files")
            header_row.operator(ClearCaptureList.bl_idname, icon='TRASH', text="Clear")
            
            # Batch import controls
            if len(selected_captures) > 0:
                batch_row = captures_box.row()
                batch_row.scale_y = 1.2  # Make it more prominent
                batch_row.operator(BatchImportSelectedCaptures.bl_idname, icon='IMPORT', text=f"Batch Import {len(selected_captures)} Selected")
            
            captures_box.separator()
            
            # List captures (limit to first 20 to avoid UI clutter)
            col = captures_box.column()
            max_display = 20
            
            for i, capture in enumerate(available_captures[:max_display]):
                row = col.row(align=True)
                
                # File info
                file_name = capture['name']
                size_mb = capture['size_mb']
                full_path = capture['full_path']
                
                # Truncate long filenames
                display_name = file_name if len(file_name) <= 25 else file_name[:22] + "..."
                
                # Show file icon based on extension
                if file_name.lower().endswith('.usd'):
                    icon = 'FILE_3D'
                elif file_name.lower().endswith('.usda'):
                    icon = 'FILE_TEXT'
                elif file_name.lower().endswith('.usdc'):
                    icon = 'FILE_CACHE'
                else:
                    icon = 'FILE'
                
                # Checkbox for batch selection
                checkbox_icon = 'CHECKBOX_HLT' if full_path in selected_captures else 'CHECKBOX_DEHLT'
                toggle_op = row.operator(ToggleCaptureSelection.bl_idname, text="", icon=checkbox_icon)
                toggle_op.capture_file_path = full_path
                
                # File name and size
                row.label(text=f"{display_name} ({size_mb:.1f}MB)", icon=icon)
                
                # Import button
                import_op = row.operator(ImportCaptureFile.bl_idname, text="", icon='IMPORT')
                import_op.capture_file_path = full_path
            
            # Show "and X more..." if there are more files
            if len(available_captures) > max_display:
                remaining = len(available_captures) - max_display
                col.label(text=f"... and {remaining} more files", icon='THREE_DOTS')
                col.label(text="(Use 'Clear' and 'Scan' to refresh)", icon='INFO')

class PT_RemixBackgroundProcessing(bpy.types.Panel):
    """Panel for background texture processing status and controls"""
    bl_label = "Background Processing"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "RTX Remix"
    bl_parent_id = "PT_RemixExport"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        
        # Get background processor status
        try:
            from .... import core_utils
            background_processor = core_utils.get_background_processor()
            
            active_count = len(background_processor.active_jobs)
            completed_count = len(background_processor.completed_jobs)
            
            # Status info
            col = layout.column(align=True)
            col.label(text=f"Active Jobs: {active_count}")
            col.label(text=f"Completed Jobs: {completed_count}")
            
            # Show active job details
            if active_count > 0:
                box = layout.box()
                box.label(text="Active Jobs:", icon='TIME')
                
                for job_id, job_info in background_processor.active_jobs.items():
                    status = background_processor.get_job_status(job_id)
                    if status:
                        progress_pct = (status['progress'] / status['total']) * 100 if status['total'] > 0 else 0
                        
                        row = box.row()
                        row.label(text=f"{job_id[-8:]}...")  # Show last 8 chars of job ID
                        row.label(text=f"{progress_pct:.0f}%")
                        
                        # Cancel button for individual job
                        op = row.operator("remix.cancel_background_job", text="", icon='X')
                        op.job_id = job_id
            
            # Control buttons
            row = layout.row(align=True)
            row.operator("remix.background_job_status", text="Refresh Status")
            
            if active_count > 0:
                row.operator("remix.cancel_all_background_jobs", text="Cancel All")
            
            if completed_count > 0:
                layout.operator("remix.cleanup_completed_jobs", text="Cleanup Completed")
            
            # Test button (for development)
            layout.separator()
            layout.operator("remix.background_processing_test", text="Test Background Processing")
            
        except Exception as e:
            layout.label(text=f"Error: {e}", icon='ERROR') 