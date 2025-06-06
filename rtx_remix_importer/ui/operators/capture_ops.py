import bpy
import os
import traceback
from ...import_core import import_rtx_remix_usd_with_materials, USDImportError

try:
    from pxr import Usd
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False


def auto_scan_capture_folder(self, context):
    """Auto-scan capture folder when path changes"""
    if USD_AVAILABLE and self.remix_capture_folder_path and context:
        # Use the scan operator to do the actual scanning
        try:
            bpy.ops.remix.scan_capture_folder()
        except:
            # If operator fails, just clear the captures list
            if hasattr(context.scene, "remix_captures"):
                context.scene.remix_captures.clear()

class ScanCaptureFolder(bpy.types.Operator):
    """Refresh the capture folder scan for available USD files"""
    bl_idname = "remix.scan_capture_folder"
    bl_label = "Refresh Capture Folder"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return USD_AVAILABLE and context.scene.remix_capture_folder_path

    def execute(self, context):
        if not USD_AVAILABLE:
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        capture_folder = bpy.path.abspath(context.scene.remix_capture_folder_path)
        if not os.path.exists(capture_folder):
            self.report({'ERROR'}, f"Capture folder not found: {capture_folder}")
            return {'CANCELLED'}

        print(f"Scanning capture folder: {capture_folder}")
        
        # Find all USD files in the capture folder (top-level only, not recursive)
        # RTX Remix capture folders typically contain thousands of individual asset USD files
        # in the root directory, so recursive scanning would be extremely slow and overwhelming
        usd_files = []
        supported_extensions = ['.usd', '.usda', '.usdc']
        
        try:
            # Only scan the top-level directory, not subdirectories
            for file in os.listdir(capture_folder):
                if any(file.lower().endswith(ext) for ext in supported_extensions):
                    full_path = os.path.join(capture_folder, file)
                    # Verify it's actually a file (not a directory with USD extension)
                    if os.path.isfile(full_path):
                        # Get file size and modification time for display
                        try:
                            stat = os.stat(full_path)
                            size_mb = stat.st_size / (1024 * 1024)
                            mod_time = stat.st_mtime
                            usd_files.append({
                                'name': file,
                                'full_path': full_path,
                                'rel_path': file,  # Just the filename since we're not going recursive
                                'size_mb': size_mb,
                                'mod_time': mod_time
                            })
                        except OSError:
                            # Skip files we can't stat
                            continue
            
            # Sort by modification time (newest first)
            usd_files.sort(key=lambda x: x['mod_time'], reverse=True)
            
            # Store the list in the scene's CollectionProperty
            context.scene.remix_captures.clear()
            for f in usd_files:
                item = context.scene.remix_captures.add()
                item.name = f['name']
                item.full_path = f['full_path']
                item.size_mb = f['size_mb']
            
            self.report({'INFO'}, f"Found {len(usd_files)} USD files in capture folder")
            print(f"Found USD files: {[f['name'] for f in usd_files[:5]]}{'...' if len(usd_files) > 5 else ''}")
            
        except Exception as e:
            self.report({'ERROR'}, f"Error scanning capture folder: {e}")
            return {'CANCELLED'}
        
        return {'FINISHED'}


class ImportCaptureFile(bpy.types.Operator):
    """Import a selected RTX Remix capture file"""
    bl_idname = "remix.import_capture"
    bl_label = "Import RTX Remix Capture"
    
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")

    def execute(self, context):
        if not self.filepath:
            self.report({'ERROR'}, "Filepath not set.")
            return {'CANCELLED'}

        try:
            # Clear material cache before import if desired
            # clear_material_cache()

            new_objects, new_lights, new_cameras, message = import_rtx_remix_usd_with_materials(
                context,
                self.filepath,
                import_materials=context.scene.remix_capture_import_materials,
                import_lights=context.scene.remix_capture_import_lights,
                scene_scale=context.scene.remix_capture_scene_scale
            )

            if new_objects is not None:
                self.report({'INFO'}, f"Imported capture: {message}")
                # Store the name of the last imported camera if one exists
                if new_cameras:
                    context.scene.remix_last_imported_camera = list(new_cameras)[0].name
            else:
                self.report({'ERROR'}, f"Failed to import capture: {message}")
                return {'CANCELLED'}

        except USDImportError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"An unexpected error occurred: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}


class ClearCaptureList(bpy.types.Operator):
    """Clear the scanned capture file list"""
    bl_idname = "remix.clear_capture_list"
    bl_label = "Clear Capture List"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        context.scene.remix_captures.clear()
        self.report({'INFO'}, "Cleared capture file list")
        return {'FINISHED'}

class ToggleCaptureSelection(bpy.types.Operator):
    """Toggle capture selection for batch import"""
    bl_idname = "remix.toggle_capture_selection"
    bl_label = "Toggle Capture Selection"
    bl_options = {'REGISTER', 'UNDO'}

    capture_file_path: bpy.props.StringProperty(
        name="Capture File Path",
        description="Full path to the capture USD file"
    )

    def execute(self, context):
        # This operator is no longer needed as the selection is handled by the UIList's property.
        # However, we can adapt it or simply remove it. For now, let's have it do nothing.
        # The checkbox in the UIList directly modifies the `is_selected` property.
        return {'FINISHED'}

class BatchImportAllCaptures(bpy.types.Operator):
    """Import all available capture files"""
    bl_idname = "remix.batch_import_all_captures"
    bl_label = "Batch Import All Captures"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        captures_to_import = context.scene.remix_captures
        if not captures_to_import:
            self.report({'ERROR'}, "No captures available to import.")
            return {'CANCELLED'}

        # Select all captures before calling the batch operator
        for capture in captures_to_import:
            capture.is_selected = True
        
        return bpy.ops.remix.batch_import_selected_captures('EXEC_DEFAULT')


class BatchImportSelectedCaptures(bpy.types.Operator):
    """Import only the selected capture files"""
    bl_idname = "remix.batch_import_selected_captures"
    bl_label = "Batch Import Selected"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(c.is_selected for c in context.scene.remix_captures)

    def execute(self, context):
        captures_to_import = [c for c in context.scene.remix_captures if c.is_selected]
        
        if not captures_to_import:
            self.report({'ERROR'}, "No captures were selected for import.")
            return {'CANCELLED'}

        total_count = len(captures_to_import)
        imported_count = 0
        total_new_objects = set()
        total_new_lights = set()

        self.report({'INFO'}, f"Batch import started for {total_count} selected captures.")
        
        for capture in captures_to_import:
            try:
                new_objects, new_lights, new_cameras, message = import_rtx_remix_usd_with_materials(
                    context,
                    capture.full_path,
                    import_materials=context.scene.remix_capture_import_materials,
                    import_lights=context.scene.remix_capture_import_lights,
                    scene_scale=context.scene.remix_capture_scene_scale
                )
                if new_objects is not None:
                    total_new_objects.update(new_objects)
                    total_new_lights.update(new_lights)
                    if new_cameras:
                        # Store the name of the most recent camera from the last successful import
                        context.scene.remix_last_imported_camera = list(new_cameras)[0].name
                    imported_count += 1
                else:
                    self.report({'WARNING'}, f"Could not import {capture.name}: {message}")
            except Exception as e:
                self.report({'ERROR'}, f"Error importing {capture.name}: {e}")

        summary_message = f"Batch import complete. Imported {imported_count}/{total_count} captures. Created {len(total_new_objects)} objects and {len(total_new_lights)} lights."
        self.report({'INFO'}, summary_message)
        
        # Clear selection after import
        for capture in captures_to_import:
            capture.is_selected = False
            
        return {'FINISHED'}
