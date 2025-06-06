import bpy
import os
import traceback

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
    """Import a specific capture USD file"""
    bl_idname = "remix.import_capture_file"
    bl_label = "Import Capture File"
    bl_options = {'REGISTER', 'UNDO'}

    capture_file_path: bpy.props.StringProperty(
        name="Capture File Path",
        description="Full path to the capture USD file to import"
    )

    @classmethod
    def poll(cls, context):
        return USD_AVAILABLE

    def execute(self, context):
        if not USD_AVAILABLE:
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        if not self.capture_file_path:
            self.report({'ERROR'}, "No capture file path provided.")
            return {'CANCELLED'}

        filepath = bpy.path.abspath(self.capture_file_path)
        if not os.path.exists(filepath):
            self.report({'ERROR'}, f"Capture file not found: {filepath}")
            return {'CANCELLED'}

        # Import the core functionality
        try:
            from ... import import_core
            
            # Use the scene properties for import settings
            scene = context.scene
            import_materials = scene.remix_capture_import_materials
            import_lights = scene.remix_capture_import_lights
            scene_scale = scene.remix_capture_scene_scale

            print(f"Importing capture file: {filepath}")
            print(f"  Settings: materials={import_materials}, lights={import_lights}, scale={scene_scale}")
            
            # Call the core import function
            imported_objects, imported_lights, message = import_core.import_rtx_remix_usd_with_materials(
                context,
                filepath,
                import_materials,
                import_lights,
                scene_scale
            )

            if imported_objects is not None:
                self.report({'INFO'}, f"Imported capture: {message}")
                print(f"Capture import finished: {message}")
                return {'FINISHED'}
            else:
                self.report({'ERROR'}, f"Capture import failed: {message}")
                print(f"Capture import failed: {message}")
                return {'CANCELLED'}
                
        except ImportError as e:
            self.report({'ERROR'}, f"Import core module not available: {e}")
            return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"Error importing capture file: {e}")
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}


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

class BatchImportCaptures(bpy.types.Operator):
    """Import multiple capture files while avoiding duplicates"""
    bl_idname = "remix.batch_import_captures"
    bl_label = "Batch Import Captures"
    bl_options = {'REGISTER', 'UNDO'}

    # Properties for batch import
    skip_duplicates: bpy.props.BoolProperty(
        name="Skip Duplicates",
        description="Skip assets that already exist in the scene based on USD prim path",
        default=True
    )
    
    merge_collections: bpy.props.BoolProperty(
        name="Merge Collections",
        description="Merge imported objects into existing collections instead of creating new ones",
        default=True
    )
    
    duplicate_detection_method: bpy.props.EnumProperty(
        name="Duplicate Detection",
        description="Method to detect duplicate assets",
        items=[
            ('USD_PATH', "USD Prim Path", "Compare USD prim paths (most accurate)"),
            ('OBJECT_NAME', "Object Name", "Compare object names (faster but less accurate)"),
            ('MESH_DATA', "Mesh Data", "Compare mesh vertex count and bounds (slowest but most thorough)")
        ],
        default='USD_PATH'
    )

    @classmethod
    def poll(cls, context):
        return USD_AVAILABLE

    def invoke(self, context, event):
        # Show properties dialog
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        
        # Get available captures from the CollectionProperty
        available_captures = context.scene.remix_captures
        
        if not available_captures:
            layout.label(text="No captures scanned. Use 'Scan' first.", icon='ERROR')
            return
        
        layout.label(text=f"Found {len(available_captures)} captures to process:")
        
        # Show settings
        box = layout.box()
        box.label(text="Import Settings:", icon='SETTINGS')
        box.prop(self, "skip_duplicates")
        box.prop(self, "merge_collections")
        box.prop(self, "duplicate_detection_method")
        
        # Show capture list preview
        box = layout.box()
        box.label(text="Captures to Import:", icon='FILE_3D')
        
        # Show first few captures
        max_preview = 5
        for i, capture in enumerate(available_captures[:max_preview]):
            row = box.row()
            row.label(text=f"• {capture.name} ({capture.size_mb:.1f}MB)", icon='FILE')
        
        if len(available_captures) > max_preview:
            remaining = len(available_captures) - max_preview
            box.label(text=f"... and {remaining} more files")

    def execute(self, context):
        if not USD_AVAILABLE:
            self.report({'ERROR'}, "USD Python libraries (pxr) not available.")
            return {'CANCELLED'}

        available_captures = context.scene.remix_captures
        if not available_captures:
            self.report({'ERROR'}, "No captures available. Scan capture folder first.")
            return {'CANCELLED'}

        scene = context.scene
        
        # Track existing objects for duplicate detection
        existing_objects = self._build_existing_objects_map(context)
        
        # Import statistics
        total_captures = len(available_captures)
        imported_count = 0
        skipped_count = 0
        error_count = 0
        new_objects = []
        
        self.report({'INFO'}, f"Starting batch import of {total_captures} captures...")
        
        try:
            from ... import import_core
            
            for i, capture in enumerate(available_captures):
                capture_name = capture.name
                capture_path = capture.full_path
                
                # Update progress
                progress = (i + 1) / total_captures * 100
                print(f"Processing capture {i+1}/{total_captures} ({progress:.1f}%): {capture_name}")
                
                try:
                    # Import the capture
                    imported_objects, imported_lights, message = import_core.import_rtx_remix_usd_with_materials(
                        context,
                        capture_path,
                        scene.remix_capture_import_materials,
                        scene.remix_capture_import_lights,
                        scene.remix_capture_scene_scale
                    )
                    
                    if imported_objects is not None:
                        # Process imported objects for duplicates
                        processed_objects = self._process_imported_objects(
                            context, imported_objects, existing_objects, capture_name
                        )
                        
                        new_objects.extend(processed_objects)
                        imported_count += 1
                        
                        print(f"  ✓ Imported {len(processed_objects)} new objects from {capture_name}")
                        
                    else:
                        print(f"  ✗ Failed to import {capture_name}: {message}")
                        error_count += 1
                        
                except Exception as e:
                    print(f"  ✗ Error importing {capture_name}: {e}")
                    error_count += 1
                    continue
            
            # Final cleanup and organization
            if self.merge_collections and new_objects:
                self._organize_imported_objects(context, new_objects)
            
            # Report results
            total_new_objects = len(new_objects)
            self.report({'INFO'}, 
                f"Batch import complete: {imported_count} captures imported, "
                f"{total_new_objects} new objects, {skipped_count} duplicates skipped, "
                f"{error_count} errors"
            )
            
            print(f"Batch import summary:")
            print(f"  Captures processed: {imported_count}/{total_captures}")
            print(f"  New objects: {total_new_objects}")
            print(f"  Duplicates skipped: {skipped_count}")
            print(f"  Errors: {error_count}")
            
            return {'FINISHED'}
            
        except ImportError as e:
            self.report({'ERROR'}, f"Import core module not available: {e}")
            return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"Error during batch import: {e}")
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}

    def _build_existing_objects_map(self, context):
        """Build a map of existing objects for duplicate detection."""
        existing_objects = {}
        
        for obj in bpy.data.objects:
            if obj.type not in ['MESH', 'LIGHT', 'CAMERA']:
                continue
                
            # Store different identifiers based on detection method
            if self.duplicate_detection_method == 'USD_PATH':
                if "usd_instance_path" in obj:
                    usd_path = obj["usd_instance_path"]
                    existing_objects[usd_path] = obj
                elif "usd_prim_path" in obj:
                    usd_path = obj["usd_prim_path"]
                    existing_objects[usd_path] = obj
                    
            elif self.duplicate_detection_method == 'OBJECT_NAME':
                # Use base name without suffixes
                base_name = obj.name.split('.')[0]  # Remove .001, .002 etc
                existing_objects[base_name] = obj
                
            elif self.duplicate_detection_method == 'MESH_DATA':
                if obj.type == 'MESH' and obj.data:
                    # Create signature based on vertex count and bounds
                    mesh = obj.data
                    vert_count = len(mesh.vertices)
                    if vert_count > 0:
                        # Get bounding box as a simple signature
                        bounds = [v for v in obj.bound_box[0]] + [v for v in obj.bound_box[6]]
                        signature = f"{vert_count}_{hash(tuple(bounds))}"
                        existing_objects[signature] = obj
        
        print(f"Built existing objects map with {len(existing_objects)} entries using {self.duplicate_detection_method}")
        return existing_objects

    def _process_imported_objects(self, context, imported_objects, existing_objects, capture_name):
        """Process imported objects to remove duplicates and track new ones."""
        new_objects = []
        
        for obj in imported_objects:
            is_duplicate = False
            
            if self.skip_duplicates:
                # Check for duplicates based on selected method
                if self.duplicate_detection_method == 'USD_PATH':
                    usd_path = obj.get("usd_instance_path") or obj.get("usd_prim_path")
                    if usd_path and usd_path in existing_objects:
                        is_duplicate = True
                        
                elif self.duplicate_detection_method == 'OBJECT_NAME':
                    base_name = obj.name.split('.')[0]
                    if base_name in existing_objects:
                        is_duplicate = True
                        
                elif self.duplicate_detection_method == 'MESH_DATA':
                    if obj.type == 'MESH' and obj.data:
                        mesh = obj.data
                        vert_count = len(mesh.vertices)
                        if vert_count > 0:
                            bounds = [v for v in obj.bound_box[0]] + [v for v in obj.bound_box[6]]
                            signature = f"{vert_count}_{hash(tuple(bounds))}"
                            if signature in existing_objects:
                                is_duplicate = True
            
            if is_duplicate:
                # Remove duplicate object
                print(f"    Removing duplicate: {obj.name}")
                bpy.data.objects.remove(obj, do_unlink=True)
            else:
                # Keep new object and add to tracking
                new_objects.append(obj)
                
                # Add to existing objects map for future duplicate detection
                if self.duplicate_detection_method == 'USD_PATH':
                    usd_path = obj.get("usd_instance_path") or obj.get("usd_prim_path")
                    if usd_path:
                        existing_objects[usd_path] = obj
                elif self.duplicate_detection_method == 'OBJECT_NAME':
                    base_name = obj.name.split('.')[0]
                    existing_objects[base_name] = obj
                elif self.duplicate_detection_method == 'MESH_DATA':
                    if obj.type == 'MESH' and obj.data:
                        mesh = obj.data
                        vert_count = len(mesh.vertices)
                        if vert_count > 0:
                            bounds = [v for v in obj.bound_box[0]] + [v for v in obj.bound_box[6]]
                            signature = f"{vert_count}_{hash(tuple(bounds))}"
                            existing_objects[signature] = obj
                
                # Tag object with source capture
                obj["remix_source_capture"] = capture_name
        
        return new_objects

    def _organize_imported_objects(self, context, new_objects):
        """Organize imported objects into collections."""
        if not new_objects:
            return
            
        # Find or create a "Batch Import" collection
        batch_collection_name = "RTX_Remix_Batch_Import"
        
        if batch_collection_name in bpy.data.collections:
            batch_collection = bpy.data.collections[batch_collection_name]
        else:
            batch_collection = bpy.data.collections.new(batch_collection_name)
            context.scene.collection.children.link(batch_collection)
        
        # Group objects by type
        mesh_objects = [obj for obj in new_objects if obj.type == 'MESH']
        light_objects = [obj for obj in new_objects if obj.type == 'LIGHT']
        camera_objects = [obj for obj in new_objects if obj.type == 'CAMERA']
        
        # Create sub-collections if needed
        for obj_type, objects in [('Meshes', mesh_objects), ('Lights', light_objects), ('Cameras', camera_objects)]:
            if not objects:
                continue
                
            sub_collection_name = f"{batch_collection_name}_{obj_type}"
            
            if sub_collection_name in bpy.data.collections:
                sub_collection = bpy.data.collections[sub_collection_name]
            else:
                sub_collection = bpy.data.collections.new(obj_type)
                batch_collection.children.link(sub_collection)
            
            # Move objects to sub-collection
            for obj in objects:
                # Remove from current collections
                for collection in obj.users_collection:
                    collection.objects.unlink(obj)
                # Add to target collection
                sub_collection.objects.link(obj)
        
        print(f"Organized {len(new_objects)} objects into {batch_collection_name} collection")

class BatchImportSelectedCaptures(bpy.types.Operator):
    """Import only the selected capture files while avoiding duplicates"""
    bl_idname = "remix.batch_import_selected_captures"
    bl_label = "Batch Import Selected Captures"
    bl_options = {'REGISTER', 'UNDO'}

    # Properties for batch import
    skip_duplicates: bpy.props.BoolProperty(
        name="Skip Duplicates",
        description="Skip assets that already exist in the scene based on USD prim path",
        default=True
    )
    
    merge_collections: bpy.props.BoolProperty(
        name="Merge Collections",
        description="Merge imported objects into existing collections instead of creating new ones",
        default=True
    )
    
    duplicate_detection_method: bpy.props.EnumProperty(
        name="Duplicate Detection",
        description="Method to detect duplicate assets",
        items=[
            ('USD_PATH', "USD Prim Path", "Compare USD prim paths (most accurate)"),
            ('OBJECT_NAME', "Object Name", "Compare object names (faster but less accurate)"),
            ('MESH_DATA', "Mesh Data", "Compare mesh vertex count and bounds (slowest but most thorough)")
        ],
        default='USD_PATH'
    )

    @classmethod
    def poll(cls, context):
        selected_captures = [c for c in context.scene.remix_captures if c.is_selected]
        return USD_AVAILABLE and len(selected_captures) > 0

    def invoke(self, context, event):
        # Show properties dialog
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        
        # Get selected captures
        selected_captures = [c for c in context.scene.remix_captures if c.is_selected]
        
        if not selected_captures:
            layout.label(text="No captures selected for batch import.", icon='ERROR')
            return
        
        layout.label(text=f"Selected {len(selected_captures)} captures to process:")
        
        # Show settings
        box = layout.box()
        box.label(text="Import Settings:", icon='SETTINGS')
        box.prop(self, "skip_duplicates")
        box.prop(self, "merge_collections")
        box.prop(self, "duplicate_detection_method")
        
        # Show capture list preview
        box = layout.box()
        box.label(text="Selected Captures:", icon='FILE_3D')
        
        # Show first few captures
        max_preview = 5
        for i, capture in enumerate(selected_captures[:max_preview]):
            row = box.row()
            row.label(text=f"• {capture.name} ({capture.size_mb:.1f}MB)", icon='FILE')
        
        if len(selected_captures) > max_preview:
            remaining = len(selected_captures) - max_preview
            box.label(text=f"... and {remaining} more files", icon='THREE_DOTS')

    def execute(self, context):
        # Get selected captures
        captures_to_import = [c for c in context.scene.remix_captures if c.is_selected]
        
        if not captures_to_import:
            self.report({'ERROR'}, "No captures selected for batch import.")
            return {'CANCELLED'}
        
        # Use the same logic as the original batch import but only for selected captures
        return self._execute_batch_import(context, captures_to_import)
    
    def _execute_batch_import(self, context, captures_to_import):
        """Execute the batch import for the given captures (reused from BatchImportCaptures)"""
        # This method contains the same logic as the original BatchImportCaptures.execute()
        # but operates on the filtered list of selected captures
        
        try:
            from ... import import_core
        except ImportError as e:
            self.report({'ERROR'}, f"Import core module not available: {e}")
            return {'CANCELLED'}

        # Get import settings from scene
        scene = context.scene
        import_materials = scene.remix_capture_import_materials
        import_lights = scene.remix_capture_import_lights
        scene_scale = scene.remix_capture_scene_scale

        # Build existing objects map for duplicate detection
        existing_objects = self._build_existing_objects_map(context) if self.skip_duplicates else {}

        # Track statistics
        total_captures = len(captures_to_import)
        imported_count = 0
        skipped_count = 0
        failed_count = 0
        total_new_objects = 0

        print(f"Starting batch import of {total_captures} selected captures...")
        
        for i, capture in enumerate(captures_to_import):
            capture_name = capture.name
            capture_path = capture.full_path
            
            print(f"Processing capture {i+1}/{total_captures}: {capture_name}")
            self.report({'INFO'}, f"Processing {i+1}/{total_captures}: {capture_name}")
            
            try:
                # Import the capture
                imported_objects, imported_lights, message = import_core.import_rtx_remix_usd_with_materials(
                    context,
                    capture_path,
                    import_materials,
                    import_lights,
                    scene_scale
                )

                if imported_objects is not None:
                    # Process imported objects for duplicates
                    new_objects = self._process_imported_objects(context, imported_objects, existing_objects, capture_name)
                    
                    if new_objects:
                        # Organize objects into collections if requested
                        if self.merge_collections:
                            self._organize_imported_objects(context, new_objects)
                        
                        total_new_objects += len(new_objects)
                        imported_count += 1
                        print(f"  Successfully imported {len(new_objects)} new objects from {capture_name}")
                    else:
                        skipped_count += 1
                        print(f"  Skipped {capture_name} - all objects were duplicates")
                else:
                    failed_count += 1
                    print(f"  Failed to import {capture_name}: {message}")
                    
            except Exception as e:
                failed_count += 1
                print(f"  Error importing {capture_name}: {e}")
                import traceback
                traceback.print_exc()

        # Final report
        summary = f"Batch import complete: {imported_count} imported, {skipped_count} skipped, {failed_count} failed. Total new objects: {total_new_objects}"
        self.report({'INFO'}, summary)
        print(summary)
        
        # Clear selection after successful batch import
        for capture in context.scene.remix_captures:
            capture.is_selected = False
        
        return {'FINISHED'}
    
    def _build_existing_objects_map(self, context):
        """Build a map of existing objects for duplicate detection (reused from BatchImportCaptures)"""
        existing_objects = {}
        
        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                if self.duplicate_detection_method == 'USD_PATH':
                    # Use USD prim path if available
                    if obj.data and "usd_prim_path" in obj.data:
                        usd_path = obj.data["usd_prim_path"]
                        existing_objects[usd_path] = obj
                elif self.duplicate_detection_method == 'OBJECT_NAME':
                    # Use object name
                    existing_objects[obj.name] = obj
                elif self.duplicate_detection_method == 'MESH_DATA':
                    # Use mesh data characteristics
                    if obj.data:
                        vertex_count = len(obj.data.vertices)
                        bounds = tuple(obj.bound_box[0]) + tuple(obj.bound_box[6])  # Min and max corners
                        key = (vertex_count, bounds)
                        existing_objects[key] = obj
        
        return existing_objects
    
    def _process_imported_objects(self, context, imported_objects, existing_objects, capture_name):
        """Process imported objects and filter out duplicates (reused from BatchImportCaptures)"""
        new_objects = []
        
        for obj in imported_objects:
            if obj.type != 'MESH':
                new_objects.append(obj)  # Always keep non-mesh objects
                continue
            
            is_duplicate = False
            
            if self.skip_duplicates:
                if self.duplicate_detection_method == 'USD_PATH':
                    # Check USD prim path
                    if obj.data and "usd_prim_path" in obj.data:
                        usd_path = obj.data["usd_prim_path"]
                        if usd_path in existing_objects:
                            is_duplicate = True
                elif self.duplicate_detection_method == 'OBJECT_NAME':
                    # Check object name
                    if obj.name in existing_objects:
                        is_duplicate = True
                elif self.duplicate_detection_method == 'MESH_DATA':
                    # Check mesh data characteristics
                    if obj.data:
                        vertex_count = len(obj.data.vertices)
                        bounds = tuple(obj.bound_box[0]) + tuple(obj.bound_box[6])
                        key = (vertex_count, bounds)
                        if key in existing_objects:
                            is_duplicate = True
            
            if is_duplicate:
                # Remove duplicate object
                bpy.data.objects.remove(obj, do_unlink=True)
                print(f"    Removed duplicate object: {obj.name}")
            else:
                new_objects.append(obj)
                # Add to existing objects map for future duplicate detection
                if self.duplicate_detection_method == 'USD_PATH' and obj.data and "usd_prim_path" in obj.data:
                    existing_objects[obj.data["usd_prim_path"]] = obj
                elif self.duplicate_detection_method == 'OBJECT_NAME':
                    existing_objects[obj.name] = obj
                elif self.duplicate_detection_method == 'MESH_DATA' and obj.data:
                    vertex_count = len(obj.data.vertices)
                    bounds = tuple(obj.bound_box[0]) + tuple(obj.bound_box[6])
                    key = (vertex_count, bounds)
                    existing_objects[key] = obj
        
        return new_objects
    
    def _organize_imported_objects(self, context, new_objects):
        """Organize imported objects into collections (reused from BatchImportCaptures)"""
        # Find or create a "Batch Import" collection
        batch_collection_name = "Batch_Import_Captures"
        batch_collection = bpy.data.collections.get(batch_collection_name)
        
        if not batch_collection:
            batch_collection = bpy.data.collections.new(batch_collection_name)
            context.scene.collection.children.link(batch_collection)
        
        # Move objects to the batch collection
        for obj in new_objects:
            # Remove from current collections
            for collection in obj.users_collection:
                collection.objects.unlink(obj)
            
            # Add to batch collection
            batch_collection.objects.link(obj)
