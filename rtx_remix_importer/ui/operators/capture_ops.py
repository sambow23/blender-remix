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
    
    # Modal state
    _timer = None
    _importing = False

    def modal(self, context, event):
        if event.type == 'TIMER':
            if not self._importing:
                # Import finished
                self._cleanup(context)
                context.workspace.status_text_set(None)
                return {'FINISHED'}
            
            return {'RUNNING_MODAL'}
        
        elif event.type == 'ESC':
            # Can't really cancel mid-import, but acknowledge ESC
            return {'RUNNING_MODAL'}
        
        return {'PASS_THROUGH'}

    def execute(self, context):
        if not self.filepath:
            self.report({'ERROR'}, "Filepath not set.")
            return {'CANCELLED'}

        try:
            import threading
            
            # Start import in background thread
            self._importing = True
            
            def do_import():
                try:
                    new_objects, new_lights, new_cameras, message = import_rtx_remix_usd_with_materials(
                        context,
                        self.filepath,
                        import_materials=context.scene.remix_capture_import_materials,
                        import_lights=context.scene.remix_capture_import_lights,
                        scene_scale=context.scene.remix_capture_scene_scale
                    )

                    if new_objects is not None:
                        self.report({'INFO'}, f"Imported capture: {message}")
                        if new_cameras:
                            context.scene.remix_last_imported_camera = list(new_cameras)[0].name
                    else:
                        self.report({'ERROR'}, f"Failed to import capture: {message}")

                except USDImportError as e:
                    self.report({'ERROR'}, str(e))
                except Exception as e:
                    self.report({'ERROR'}, f"An unexpected error occurred: {e}")
                finally:
                    self._importing = False
            
            import_thread = threading.Thread(target=do_import, daemon=True)
            import_thread.start()
            
            # Setup modal timer
            wm = context.window_manager
            self._timer = wm.event_timer_add(0.1, window=context.window)
            wm.modal_handler_add(self)
            
            import os
            context.workspace.status_text_set(f"Importing {os.path.basename(self.filepath)}...")
            
            return {'RUNNING_MODAL'}

        except Exception as e:
            self.report({'ERROR'}, f"Error starting import: {e}")
            return {'CANCELLED'}
    
    def _cleanup(self, context):
        """Clean up modal resources."""
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None


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

    # Modal operator state
    _timer = None
    _captures_to_import = None
    _current_index = 0
    _total_count = 0
    _imported_count = 0
    _total_new_objects = None
    _total_new_lights = None
    _viewport_state = None

    @classmethod
    def poll(cls, context):
        return any(c.is_selected for c in context.scene.remix_captures)

    def modal(self, context, event):
        if event.type == 'TIMER':
            if self._current_index >= self._total_count:
                # All captures imported, finish
                self._cleanup(context)
                
                summary_message = f"Batch import complete. Imported {self._imported_count}/{self._total_count} captures. Created {len(self._total_new_objects)} objects and {len(self._total_new_lights)} lights."
                self.report({'INFO'}, summary_message)
                
                context.workspace.status_text_set(None)
                
                # Clear selection after import
                for capture in self._captures_to_import:
                    capture.is_selected = False
                
                return {'FINISHED'}
            
            # Import one capture per tick
            capture = self._captures_to_import[self._current_index]
            
            try:
                new_objects, new_lights, new_cameras, message = import_rtx_remix_usd_with_materials(
                    context,
                    capture.full_path,
                    import_materials=context.scene.remix_capture_import_materials,
                    import_lights=context.scene.remix_capture_import_lights,
                    scene_scale=context.scene.remix_capture_scene_scale
                )
                if new_objects is not None:
                    self._total_new_objects.update(new_objects)
                    self._total_new_lights.update(new_lights)
                    if new_cameras:
                        context.scene.remix_last_imported_camera = list(new_cameras)[0].name
                    self._imported_count += 1
                else:
                    self.report({'WARNING'}, f"Could not import {capture.name}: {message}")
            except Exception as e:
                self.report({'ERROR'}, f"Error importing {capture.name}: {e}")
            
            self._current_index += 1
            
            # Update progress
            progress = (self._current_index / self._total_count) * 100
            context.workspace.status_text_set(f"Importing captures: {self._current_index}/{self._total_count} ({progress:.0f}%)")
            
            return {'RUNNING_MODAL'}
        
        elif event.type == 'ESC':
            # User cancelled
            self._cleanup(context)
            self.report({'WARNING'}, f"Batch import cancelled. Imported {self._imported_count}/{self._total_count} captures.")
            context.workspace.status_text_set(None)
            return {'CANCELLED'}
        
        return {'PASS_THROUGH'}

    def execute(self, context):
        self._captures_to_import = [c for c in context.scene.remix_captures if c.is_selected]
        
        if not self._captures_to_import:
            self.report({'ERROR'}, "No captures were selected for import.")
            return {'CANCELLED'}

        self._total_count = len(self._captures_to_import)
        self._current_index = 0
        self._imported_count = 0
        self._total_new_objects = set()
        self._total_new_lights = set()

        self.report({'INFO'}, f"Batch import started for {self._total_count} selected captures.")
        
        # Disable viewport updates for performance
        self._disable_viewport_updates(context)
        
        # Setup modal timer
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)  # 10Hz
        wm.modal_handler_add(self)
        
        context.workspace.status_text_set(f"Importing captures: 0/{self._total_count}")
        
        return {'RUNNING_MODAL'}
    
    def _disable_viewport_updates(self, context):
        """Disable viewport updates for better performance."""
        self._viewport_state = {
            'use_simplify': context.scene.render.use_simplify,
            'simplify_subdivision': context.scene.render.simplify_subdivision,
        }
        context.scene.render.use_simplify = True
        context.scene.render.simplify_subdivision = 0
    
    def _enable_viewport_updates(self, context):
        """Re-enable viewport updates."""
        if self._viewport_state:
            context.scene.render.use_simplify = self._viewport_state['use_simplify']
            context.scene.render.simplify_subdivision = self._viewport_state['simplify_subdivision']
        
        # Force viewport refresh
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    
    def _cleanup(self, context):
        """Clean up modal operator resources."""
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        
        self._enable_viewport_updates(context)
