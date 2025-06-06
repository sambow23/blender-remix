import bpy
import os
import traceback

class ClearMaterialCache(bpy.types.Operator):
    """Clear material and texture caches to resolve duplicate issues"""
    bl_idname = "remix.clear_material_cache"
    bl_label = "Clear Material Cache"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            # Clear material caches
            from ... import material_utils
            material_utils.clear_material_cache()
            
            # Clear texture cache
            from ... import texture_loader
            texture_loader.clear_texture_cache()
            
            # Clean up duplicate textures
            removed_count = texture_loader.cleanup_duplicate_textures()
            
            self.report({'INFO'}, f"Cleared caches and removed {removed_count} duplicate textures")
            print("Material and texture caches cleared")
            
        except Exception as e:
            self.report({'ERROR'}, f"Error clearing caches: {e}")
            return {'CANCELLED'}
        
        return {'FINISHED'}


class FixBrokenTextures(bpy.types.Operator):
    """Convert DDS textures to PNG and update materials to use converted textures"""
    bl_idname = "remix.fix_broken_textures"
    bl_label = "Fix Broken Textures"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Convert DDS textures to PNG format using texconv.exe and update materials to use the converted textures"

    @classmethod
    def poll(cls, context):
        return context.scene.remix_capture_folder_path and os.path.exists(context.scene.remix_capture_folder_path)

    def execute(self, context):
        capture_folder = bpy.path.abspath(context.scene.remix_capture_folder_path)
        textures_dir = os.path.join(capture_folder, "textures")
        
        if not os.path.exists(textures_dir):
            self.report({'ERROR'}, f"Textures directory not found: {textures_dir}")
            return {'CANCELLED'}
        
        # Use unified TextureProcessor
        from ... import core_utils
        texture_processor = core_utils.get_texture_processor()
        
        if not texture_processor.is_available():
            self.report({'ERROR'}, "texconv.exe not found. Cannot convert DDS textures.")
            return {'CANCELLED'}
        
        print(f"Starting texture conversion process...")
        print(f"Textures directory: {textures_dir}")
        print(f"Using texconv.exe: {texture_processor.texconv_path}")
        
        try:
            # Step 1: Scan for DDS files
            dds_files = []
            for root, dirs, files in os.walk(textures_dir):
                for file in files:
                    if file.lower().endswith('.dds'):
                        dds_files.append(os.path.join(root, file))
            
            if not dds_files:
                self.report({'INFO'}, "No DDS files found in textures directory")
                return {'FINISHED'}
            
            print(f"Found {len(dds_files)} DDS files to convert")
            
            # Step 2: Create converted directory
            converted_dir = os.path.join(textures_dir, "converted")
            os.makedirs(converted_dir, exist_ok=True)
            
            # Step 3: Convert DDS files to PNG using unified TextureProcessor
            def progress_callback(current, total, message):
                print(f"Converting {current+1}/{total}: {message}")
            
            converted_files = texture_processor.batch_convert_dds_to_png(
                dds_files, 
                converted_dir, 
                progress_callback=progress_callback
            )
            
            successful_conversions = len(converted_files)
            failed_conversions = len(dds_files) - successful_conversions
            
            print(f"Conversion complete: {successful_conversions} successful, {failed_conversions} failed")
            
            if successful_conversions == 0:
                self.report({'ERROR'}, "No textures were successfully converted")
                return {'CANCELLED'}
            
            # Step 4: Update materials to use converted textures
            # Create mapping from original DDS to converted PNG
            converted_files_map = {}
            for dds_file in dds_files:
                base_name = os.path.splitext(os.path.basename(dds_file))[0]
                png_path = os.path.join(converted_dir, f"{base_name}.png")
                if os.path.exists(png_path):
                    converted_files_map[dds_file] = png_path
            
            updated_materials = self._update_materials_with_converted_textures(converted_files_map, textures_dir, converted_dir)
            
            # Final report
            message = f"Converted {successful_conversions} textures and updated {updated_materials} materials"
            if failed_conversions > 0:
                message += f" ({failed_conversions} conversions failed)"
            
            self.report({'INFO'}, message)
            print(f"Texture fix complete: {message}")
            
            return {'FINISHED'}
            
        except Exception as e:
            self.report({'ERROR'}, f"Error during texture conversion: {e}")
            print(f"Error during texture conversion: {e}")
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}
    
    def _update_materials_with_converted_textures(self, converted_files, textures_dir, converted_dir):
        """Update all materials to use converted PNG textures instead of DDS"""
        updated_count = 0
        
        # Get all materials in the scene
        for material in bpy.data.materials:
            if not material.use_nodes:
                continue
            
            material_updated = False
            
            # Look for image texture nodes
            for node in material.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    image = node.image
                    
                    # Check if this image uses a DDS file that we converted
                    if image.filepath:
                        abs_image_path = bpy.path.abspath(image.filepath)
                        
                        # Find matching converted file
                        for original_dds, converted_png in converted_files.items():
                            # Check exact path match or filename match
                            dds_filename = os.path.basename(original_dds)
                            image_filename = os.path.basename(abs_image_path)
                            
                            if (abs_image_path == original_dds or 
                                image_filename == dds_filename or
                                image_filename.lower() == dds_filename.lower()):
                                
                                # Create new image with PNG file
                                try:
                                    # Generate a unique name for the PNG version
                                    base_name = os.path.splitext(image.name)[0]
                                    new_image_name = f"{base_name}_png"
                                    
                                    # Remove existing image with same name if it exists
                                    if new_image_name in bpy.data.images:
                                        bpy.data.images.remove(bpy.data.images[new_image_name])
                                    
                                    # Load the converted PNG
                                    new_image = bpy.data.images.load(converted_png)
                                    new_image.name = new_image_name
                                    
                                    # Replace the image in the node
                                    old_image_name = image.name
                                    node.image = new_image
                                    
                                    print(f"  Updated material '{material.name}' node '{node.name}':")
                                    print(f"    From: {old_image_name} ({dds_filename})")
                                    print(f"    To: {new_image_name} ({os.path.basename(converted_png)})")
                                    
                                    material_updated = True
                                    
                                except Exception as e:
                                    print(f"  Error updating image in material '{material.name}': {e}")
                                
                                break
            
            if material_updated:
                updated_count += 1
        
        return updated_count 