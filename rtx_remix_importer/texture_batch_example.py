"""
Example script demonstrating parallel texture processing with the RTX Remix Importer.

This script shows how to:
1. Process multiple textures in parallel using the queue system
2. Monitor progress and status
3. Handle batch operations efficiently
"""

import bpy
import asyncio
import os
from typing import List, Tuple, Optional, Callable
from .core_utils import get_texture_processor, get_texture_queue


async def process_material_textures_parallel(
    material: bpy.types.Material,
    output_dir: str,
    progress_callback: Optional[Callable[[str], None]] = None
) -> dict:
    """
    Process all textures from a material in parallel.
    
    Args:
        material: Blender material to process
        output_dir: Directory to save converted textures
        progress_callback: Optional callback for progress updates
        
    Returns:
        Dictionary with conversion results
    """
    if not material or not material.use_nodes:
        return {}
    
    texture_processor = get_texture_processor()
    
    # Collect all texture nodes from the material
    texture_tasks = []
    for node in material.node_tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image:
            bl_image = node.image
            
            # Determine texture type based on node connections or name
            texture_type = determine_texture_type(node, material)
            
            # Generate output path
            base_name = os.path.splitext(bl_image.name)[0]
            suffix = texture_processor.get_texture_suffix(texture_type)
            output_path = os.path.join(output_dir, f"{base_name}{suffix}.dds")
            
            texture_tasks.append((bl_image, output_path, texture_type, None))
    
    if not texture_tasks:
        if progress_callback:
            progress_callback(f"No textures found in material {material.name}")
        return {}
    
    if progress_callback:
        progress_callback(f"Processing {len(texture_tasks)} textures from material {material.name}")
    
    # Process textures in parallel
    results = await texture_processor.process_textures_parallel(
        texture_tasks, 
        progress_callback,
        timeout=300  # 5 minute timeout
    )
    
    return results


async def batch_convert_scene_textures(
    output_dir: str,
    selected_only: bool = False,
    progress_callback: Optional[Callable[[str], None]] = None
) -> dict:
    """
    Convert all textures in the scene (or selected objects) to DDS format in parallel.
    
    Args:
        output_dir: Directory to save converted textures
        selected_only: If True, only process textures from selected objects
        progress_callback: Optional callback for progress updates
        
    Returns:
        Dictionary with conversion results
    """
    texture_processor = get_texture_processor()
    
    if not texture_processor.is_available():
        raise RuntimeError("texconv.exe not found")
    
    # Collect all unique textures
    unique_textures = {}  # image_name -> (bl_image, texture_type)
    
    objects_to_process = bpy.context.selected_objects if selected_only else bpy.data.objects
    
    for obj in objects_to_process:
        if obj.type == 'MESH' and obj.data.materials:
            for material in obj.data.materials:
                if material and material.use_nodes:
                    for node in material.node_tree.nodes:
                        if node.type == 'TEX_IMAGE' and node.image:
                            bl_image = node.image
                            texture_type = determine_texture_type(node, material)
                            
                            # Use image name as key to avoid duplicates
                            if bl_image.name not in unique_textures:
                                unique_textures[bl_image.name] = (bl_image, texture_type)
    
    if not unique_textures:
        if progress_callback:
            progress_callback("No textures found to convert")
        return {}
    
    # Prepare tasks
    texture_tasks = []
    for image_name, (bl_image, texture_type) in unique_textures.items():
        base_name = os.path.splitext(image_name)[0]
        suffix = texture_processor.get_texture_suffix(texture_type)
        output_path = os.path.join(output_dir, f"{base_name}{suffix}.dds")
        
        texture_tasks.append((bl_image, output_path, texture_type, None))
    
    if progress_callback:
        progress_callback(f"Starting batch conversion of {len(texture_tasks)} unique textures")
    
    # Process all textures in parallel
    results = await texture_processor.process_textures_parallel(
        texture_tasks,
        progress_callback,
        timeout=600  # 10 minute timeout for large batches
    )
    
    return results


def determine_texture_type(node: bpy.types.ShaderNodeTexImage, material: bpy.types.Material) -> str:
    """
    Determine the texture type based on node connections and naming.
    
    Args:
        node: Texture image node
        material: Material containing the node
        
    Returns:
        Texture type string
    """
    # Check node label or name for hints
    node_name = (node.label or node.name).lower()
    image_name = node.image.name.lower() if node.image else ""
    
    # Common naming patterns
    if any(term in node_name or term in image_name for term in ['normal', 'norm', 'nrm']):
        return 'normal'
    elif any(term in node_name or term in image_name for term in ['rough', 'roughness']):
        return 'roughness'
    elif any(term in node_name or term in image_name for term in ['metal', 'metallic']):
        return 'metallic'
    elif any(term in node_name or term in image_name for term in ['emit', 'emission', 'emissive']):
        return 'emission'
    elif any(term in node_name or term in image_name for term in ['opacity', 'alpha']):
        return 'opacity'
    
    # Check connections to determine type
    if node.outputs and node.outputs[0].is_linked:
        for link in node.outputs[0].links:
            socket_name = link.to_socket.name.lower()
            
            if 'normal' in socket_name:
                return 'normal'
            elif 'rough' in socket_name:
                return 'roughness'
            elif 'metal' in socket_name:
                return 'metallic'
            elif any(term in socket_name for term in ['emit', 'emission']):
                return 'emission'
            elif 'alpha' in socket_name:
                return 'opacity'
    
    # Default to base color
    return 'base color'


# Example usage functions that can be called from Blender operators

def run_parallel_texture_conversion_example():
    """Example function showing how to use parallel texture conversion."""
    
    async def main():
        output_dir = "C:/temp/converted_textures"  # Change this path
        os.makedirs(output_dir, exist_ok=True)
        
        def progress_print(message):
            print(f"[Texture Conversion] {message}")
        
        try:
            # Convert all textures in the scene
            results = await batch_convert_scene_textures(
                output_dir=output_dir,
                selected_only=False,
                progress_callback=progress_print
            )
            
            # Print results
            successful = sum(1 for success in results.values() if success)
            total = len(results)
            print(f"Conversion completed: {successful}/{total} textures successful")
            
            # Show queue status
            texture_processor = get_texture_processor()
            queue_status = texture_processor.get_queue_status()
            print(f"Queue status: {queue_status}")
            
        except Exception as e:
            print(f"Error during conversion: {e}")
    
    # Run the async function
    asyncio.run(main())


def run_material_texture_conversion_example():
    """Example function showing how to convert textures from a specific material."""
    
    async def main():
        # Get active material
        if not bpy.context.active_object or not bpy.context.active_object.active_material:
            print("No active material found")
            return
        
        material = bpy.context.active_object.active_material
        output_dir = "C:/temp/material_textures"  # Change this path
        os.makedirs(output_dir, exist_ok=True)
        
        def progress_print(message):
            print(f"[Material Textures] {message}")
        
        try:
            results = await process_material_textures_parallel(
                material=material,
                output_dir=output_dir,
                progress_callback=progress_print
            )
            
            successful = sum(1 for success in results.values() if success)
            total = len(results)
            print(f"Material texture conversion completed: {successful}/{total} successful")
            
        except Exception as e:
            print(f"Error during material texture conversion: {e}")
    
    # Run the async function
    asyncio.run(main())


if __name__ == "__main__":
    # Example usage - uncomment the function you want to test
    # run_parallel_texture_conversion_example()
    # run_material_texture_conversion_example()
    pass 